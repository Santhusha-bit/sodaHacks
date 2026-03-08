"""
gemini_vision.py — Re-implemented using Google Cloud Vision API.
Keeps the same interface (GeminiVision, GeminiScene) to avoid breaking the agent.
"""
import base64
import json
import logging
import time
import requests
import cv2
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Urgency mapping for common objects detected by Vision API
URGENCY_MAP = {
    "car": "danger",
    "bus": "danger",
    "truck": "danger",
    "motorcycle": "danger",
    "bicycle": "caution",
    "person": "caution",
    "traffic light": "caution",
    "stop sign": "danger",
}

@dataclass
class GeminiObject:
    label: str
    urgency: str            # "danger" | "caution" | "calm"
    position: str           # "left" | "center" | "right"
    distance_hint: str
    speech: str

@dataclass
class GeminiScene:
    objects: list[GeminiObject] = field(default_factory=list)
    traffic_light: Optional[str] = None
    walk_signal: Optional[bool] = None
    safe_to_cross: bool = False
    scene_summary: str = ""
    timestamp: float = field(default_factory=time.time)
    raw_response: str = ""
    error: Optional[str] = None

    @property
    def dominant_urgency(self) -> str:
        if any(o.urgency == "danger" for o in self.objects):
            return "danger"
        if any(o.urgency == "caution" for o in self.objects):
            return "caution"
        return "calm"

class GeminiVision:
    def __init__(self, api_key: str, mock: bool = False):
        self.api_key = api_key
        self.mock = mock
        self.url = f"https://vision.googleapis.com/v1/images:annotate?key={self.api_key}"
        
        # Check for placeholder keys
        if self.mock or not self.api_key or "your_gemini" in self.api_key:
            self.mock = True
            logger.info("[Vision] 🎭 Running in MOCK mode")
        else:
            logger.info("[Vision] ✅ Google Cloud Vision API initialized")

    def analyze_frame(self, frame) -> GeminiScene:
        if self.mock:
            return self._mock_scene()

        try:
            _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            content = base64.b64encode(buffer).decode("utf-8")

            request_body = {
                "requests": [{
                    "image": {"content": content},
                    "features": [
                        {"type": "OBJECT_LOCALIZATION", "maxResults": 10},
                        {"type": "LABEL_DETECTION", "maxResults": 5}
                    ]
                }]
            }

            response = requests.post(self.url, json=request_body, timeout=5)
            response.raise_for_status()
            data = response.json()

            return self._parse(data)
        except Exception as e:
            logger.error(f"[Vision] API error: {e}")
            return GeminiScene(error=str(e), scene_summary="Vision AI currently unavailable.")

    def _parse(self, data) -> GeminiScene:
        try:
            res = data["responses"][0]
            gemini_objects = []
            
            objects = res.get("localizedObjectAnnotations", [])
            for obj in objects:
                label = obj["name"].lower()
                conf = obj["score"]
                
                # Position
                vertices = obj.get("boundingPoly", {}).get("normalizedVertices", [])
                pos_str = "center"
                if vertices:
                    avg_x = sum(v.get("x", 0.5) for v in vertices) / len(vertices)
                    if avg_x < 0.33: pos_str = "left"
                    elif avg_x > 0.66: pos_str = "right"
                
                # Distance
                dist_hint = "far"
                if len(vertices) >= 2:
                    xs = [v.get("x", 0.5) for v in vertices]
                    width = max(xs) - min(xs)
                    if width > 0.4: dist_hint = "very close"
                    elif width > 0.15: dist_hint = "close"
                
                urgency = URGENCY_MAP.get(label, "calm")
                speech = f"{label.capitalize()} {pos_str}, {dist_hint}." if urgency != "calm" else ""

                gemini_objects.append(GeminiObject(
                    label=label,
                    urgency=urgency,
                    position=pos_str,
                    distance_hint=dist_hint,
                    speech=speech
                ))

            # Scene summary from labels
            labels = [l["description"] for l in res.get("labelAnnotations", [])[:3]]
            summary = "Looking at: " + ", ".join(labels) if labels else "Clear path"

            return GeminiScene(
                objects=gemini_objects,
                scene_summary=summary,
                safe_to_cross=False # Harder to tell without Gemini's logic, defaulting safe
            )
        except Exception as e:
            logger.error(f"[Vision] Parse error: {e}")
            return GeminiScene(error="parse error")

    def _mock_scene(self) -> GeminiScene:
        import random
        scenarios = [
            GeminiScene(scene_summary="Mock: Clear path ahead.", objects=[]),
            GeminiScene(scene_summary="Mock: Car detected.", objects=[
                GeminiObject("car", "danger", "center", "close", "Car directly ahead.")
            ]),
        ]
        return random.choice(scenarios)
