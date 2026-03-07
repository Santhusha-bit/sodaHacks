"""
vision_detector.py — Real-time object detection for blind pedestrian navigation.

Uses YOLOv8 (ultralytics) to detect:
  - Vehicles (car, truck, bus, motorcycle)
  - Traffic lights (red, green, yellow)
  - Pedestrian crossing signals (walk/don't-walk)
  - People and cyclists

Each detected object becomes a Detection dataclass with:
  - class name
  - confidence score
  - bounding box (normalized 0-1)
  - estimated distance category (immediate/near/medium/far)
  - screen position (left/center/right and top/bottom)

The detector runs on frames received from the phone camera stream.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
import time

logger = logging.getLogger(__name__)

# ── Classes we care about and their priority ──────────────────────────────────
VEHICLE_CLASSES   = {"car", "truck", "bus", "motorcycle", "bicycle"}
PERSON_CLASSES    = {"person"}
SIGNAL_CLASSES    = {"traffic light"}          # YOLO's built-in class
STOP_SIGN_CLASSES = {"stop sign"}

# Distance estimation from bounding box height (fraction of frame height)
# Larger bbox = closer object
DISTANCE_ZONES = [
    (0.60, "immediate"),   # >60% of frame height → <3ft away, stop now
    (0.35, "near"),        # 35-60% → 3-10ft, caution
    (0.15, "medium"),      # 15-35% → 10-30ft, be aware
    (0.00, "far"),         # <15%  → >30ft, safe distance
]

URGENCY_BY_ZONE = {
    "immediate": "danger",
    "near":      "caution",
    "medium":    "caution",
    "far":       "calm",
}

FEET_BY_ZONE = {
    "immediate": "less than 3 feet",
    "near":      "about 5 to 10 feet",
    "medium":    "about 10 to 30 feet",
    "far":       "more than 30 feet",
}

try:
    from ultralytics import YOLO as _YOLO
    import numpy as np
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    logger.warning("[Vision] ultralytics not installed — running in mock mode. "
                   "Install with: pip install ultralytics")


@dataclass
class Detection:
    label: str                      # e.g. "car", "traffic light", "person"
    confidence: float               # 0.0–1.0
    bbox: tuple[float, float, float, float]  # (x1,y1,x2,y2) normalized 0-1
    distance_zone: str              # "immediate" | "near" | "medium" | "far"
    h_position: str                 # "left" | "center" | "right"
    category: str                   # "vehicle" | "person" | "signal" | "sign"
    urgency: str                    # "danger" | "caution" | "calm"

    @property
    def feet_description(self) -> str:
        return FEET_BY_ZONE.get(self.distance_zone, "nearby")

    def to_speech(self) -> str:
        """Convert detection into a natural spoken alert."""
        pos = f"to your {self.h_position}" if self.h_position != "center" else "ahead"
        dist = self.feet_description
        label = self.label

        if self.category == "vehicle":
            return f"{label.capitalize()} {pos}, {dist}."
        elif self.category == "signal":
            return f"Traffic light detected {pos}."
        elif self.category == "person":
            return f"Person {pos}, {dist}."
        elif self.category == "sign":
            return f"Stop sign {pos}."
        return f"{label} detected {pos}."


@dataclass
class SceneAnalysis:
    """Full parsed scene from one video frame."""
    detections: list[Detection] = field(default_factory=list)
    traffic_light_color: Optional[str] = None     # "red" | "green" | "unknown"
    walk_signal: Optional[bool] = None            # True = walk, False = don't walk
    safe_to_cross: bool = False
    dominant_urgency: str = "calm"
    frame_timestamp: float = field(default_factory=time.time)

    @property
    def vehicles_nearby(self) -> list[Detection]:
        return [d for d in self.detections
                if d.category == "vehicle" and d.distance_zone in ("immediate", "near")]

    @property
    def crossing_speech(self) -> Optional[str]:
        """Primary speech message for crossing decision."""
        if self.walk_signal is True and not self.vehicles_nearby:
            return "Walk signal is on. Path appears clear. Safe to cross."
        elif self.walk_signal is False:
            return "Don't walk signal. Wait at the curb."
        elif self.vehicles_nearby:
            closest = self.vehicles_nearby[0]
            return f"Do not cross. {closest.to_speech()}"
        return None


def _estimate_distance(bbox_height: float) -> str:
    for threshold, zone in DISTANCE_ZONES:
        if bbox_height >= threshold:
            return zone
    return "far"


def _h_position(cx: float) -> str:
    if cx < 0.35:
        return "left"
    elif cx > 0.65:
        return "right"
    return "center"


def _categorize(label: str) -> str:
    if label in VEHICLE_CLASSES:   return "vehicle"
    if label in PERSON_CLASSES:    return "person"
    if label in SIGNAL_CLASSES:    return "signal"
    if label in STOP_SIGN_CLASSES: return "sign"
    return "other"


class VisionDetector:
    """
    Runs YOLOv8n (nano) on each video frame.
    Returns a SceneAnalysis object per frame.
    """

    def __init__(self, model_name: str = "yolov8n.pt", mock: bool = False):
        self.mock = mock or not YOLO_AVAILABLE
        self._model = None
        self._model_name = model_name

        if not self.mock:
            self._load_model()

    def _load_model(self):
        try:
            self._model = _YOLO(self._model_name)
            logger.info(f"[Vision] ✅ YOLOv8 model loaded: {self._model_name}")
        except Exception as e:
            logger.error(f"[Vision] Model load failed: {e} — switching to mock")
            self.mock = True

    def analyze_frame(self, frame) -> SceneAnalysis:
        """
        Run detection on a single frame (numpy array HxWxC, BGR).
        Returns SceneAnalysis.
        """
        if self.mock:
            return self._mock_scene()

        try:
            results = self._model.predict(frame, conf=0.40, verbose=False)
            return self._parse_results(results[0], frame.shape)
        except Exception as e:
            logger.error(f"[Vision] Detection error: {e}")
            return SceneAnalysis()

    def _parse_results(self, result, shape) -> SceneAnalysis:
        import numpy as np
        H, W = shape[:2]
        detections = []
        traffic_light_color = None

        for box in result.boxes:
            label = result.names[int(box.cls[0])].lower()
            conf  = float(box.conf[0])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]

            # Normalize to 0-1
            nx1, ny1 = x1 / W, y1 / H
            nx2, ny2 = x2 / W, y2 / H
            bbox_h = ny2 - ny1
            cx     = (nx1 + nx2) / 2

            cat   = _categorize(label)
            zone  = _estimate_distance(bbox_h)
            hpos  = _h_position(cx)
            urgency = URGENCY_BY_ZONE[zone]

            det = Detection(
                label=label,
                confidence=conf,
                bbox=(nx1, ny1, nx2, ny2),
                distance_zone=zone,
                h_position=hpos,
                category=cat,
                urgency=urgency,
            )
            detections.append(det)

            # Traffic light color heuristic: sample pixel above the bbox center
            if cat == "signal":
                traffic_light_color = self._classify_light_color(
                    result, int(x1), int(y1), int(x2), int(y2)
                )

        # Sort by urgency (danger first) then distance
        _URGENCY_ORDER = {"danger": 0, "caution": 1, "calm": 2}
        detections.sort(key=lambda d: (_URGENCY_ORDER[d.urgency], d.distance_zone))

        dominant = detections[0].urgency if detections else "calm"

        scene = SceneAnalysis(
            detections=detections,
            traffic_light_color=traffic_light_color,
            safe_to_cross=self._is_safe_to_cross(detections, traffic_light_color),
            dominant_urgency=dominant,
        )
        return scene

    def _classify_light_color(self, result, x1, y1, x2, y2) -> str:
        """
        Simple color classification for traffic light bbox.
        Splits the bbox into thirds and checks dominant hue in each third.
        """
        try:
            import cv2, numpy as np
            frame = result.orig_img
            crop = frame[y1:y2, x1:x2]
            h = crop.shape[0] // 3
            sections = {"red": crop[:h], "yellow": crop[h:2*h], "green": crop[2*h:]}
            best_color = "unknown"
            best_score = 0
            for color, region in sections.items():
                hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
                if color == "red":
                    mask = cv2.inRange(hsv, (0,100,100), (10,255,255))
                elif color == "yellow":
                    mask = cv2.inRange(hsv, (20,100,100), (35,255,255))
                else:  # green
                    mask = cv2.inRange(hsv, (40,80,80), (90,255,255))
                score = int(mask.sum())
                if score > best_score:
                    best_score = score
                    best_color = color
            return best_color
        except Exception:
            return "unknown"

    @staticmethod
    def _is_safe_to_cross(detections: list[Detection], light_color: Optional[str]) -> bool:
        vehicles_close = any(
            d.category == "vehicle" and d.distance_zone in ("immediate", "near")
            for d in detections
        )
        return (light_color == "green") and not vehicles_close

    def _mock_scene(self) -> SceneAnalysis:
        """Return simulated scene for testing without a real camera."""
        import random
        scenarios = [
            SceneAnalysis(safe_to_cross=True, traffic_light_color="green",
                          dominant_urgency="calm",
                          detections=[]),
            SceneAnalysis(
                safe_to_cross=False,
                traffic_light_color="red",
                dominant_urgency="danger",
                detections=[Detection("car", 0.92, (0.2,0.4,0.8,0.9),
                                      "near", "center", "vehicle", "danger")]
            ),
            SceneAnalysis(
                safe_to_cross=False,
                traffic_light_color="unknown",
                dominant_urgency="caution",
                detections=[Detection("person", 0.85, (0.1,0.3,0.4,0.7),
                                      "medium", "left", "person", "caution")]
            ),
        ]
        scene = random.choice(scenarios)
        logger.info(f"[MockVision] 🎭 Scenario: safe={scene.safe_to_cross}, "
                    f"light={scene.traffic_light_color}, "
                    f"urgency={scene.dominant_urgency}")
        return scene
