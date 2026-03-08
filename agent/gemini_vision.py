"""
gemini_vision.py — Official Google Gemini Vision implementation.
Uses the new google-genai SDK for real-time pedestrian safety analysis.
"""
import base64
import json
import logging
import time
import cv2
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

PEDESTRIAN_PROMPT = """You are a real-time vision assistant for a blind pedestrian at a crosswalk.
Analyze this camera frame and return ONLY a JSON object (no markdown, no explanation) using this schema:

{
  "objects": [
    {
      "label": "car",
      "urgency": "danger",
      "position": "center",
      "distance_hint": "close",
      "speech": "Car directly ahead, about 8 feet away."
    }
  ],
  "traffic_light": "red",
  "walk_signal": false,
  "safe_to_cross": false,
  "scene_summary": "Red car approaching. Red light. Do not cross."
}

Rules:
- urgency: "danger" | "caution" | "calm"
- position: "left" | "center" | "right"
- distance_hint: "very close" (<3ft), "close" (3-10ft), "moderate" (10-30ft), "far" (>30ft)
- Only include objects relevant to pedestrian safety
- List danger objects first
"""

# ── Data model ────────────────────────────────────────────────────────────────

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

# ── Detector ──────────────────────────────────────────────────────────────────

class GeminiVision:
    def __init__(self, api_key: str, mock: bool = False):
        real_key = bool(api_key) and not api_key.startswith("your_")
        self.mock = mock or not real_key
        self._client = None
        self.api_key = api_key

        if not self.mock:
            self._init_client(api_key)

    def _init_client(self, api_key: str):
        try:
            from google import genai
            # Explicitly force API version v1 to avoid 404s in v1beta
            self._client = genai.Client(
                api_key=api_key,
            )
            logger.info("[Gemini] ✅ Gemini 2.5 Flash ready (v1beta API)")
        except Exception as e:
            logger.error(f"[Gemini] SDK Init failed: {e}")
            self.mock = True

    def analyze_frame(self, frame) -> GeminiScene:
        if self.mock:
            return self._mock_scene()

        try:
            from google.genai import types
            
            # Encode frame
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            img_bytes = bytes(buf)

            # Generate content using Gemini 2.5 Flash
            response = self._client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    PEDESTRIAN_PROMPT,
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                ]
            )

            raw_text = response.text.strip() if response.text else ""
            return self._parse_json(raw_text)

        except Exception as e:
            logger.error(f"[Gemini] API error: {e}")
            return GeminiScene(error=str(e), scene_summary="Gemini is currently unavailable.")

    def _parse_json(self, raw: str) -> GeminiScene:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])

        try:
            data = json.loads(cleaned)
            objects = [
                GeminiObject(
                    label=o.get("label", "object"),
                    urgency=o.get("urgency", "caution"),
                    position=o.get("position", "center"),
                    distance_hint=o.get("distance_hint", "unknown"),
                    speech=o.get("speech", ""),
                )
                for o in data.get("objects", [])
            ]

            return GeminiScene(
                objects=objects,
                traffic_light=data.get("traffic_light"),
                walk_signal=data.get("walk_signal"),
                safe_to_cross=bool(data.get("safe_to_cross", False)),
                scene_summary=data.get("scene_summary", ""),
                raw_response=raw,
            )
        except Exception as e:
            logger.warning(f"[Gemini] JSON Parse failed: {e}")
            return GeminiScene(error=str(e), scene_summary="Error parsing vision data.")

    def _mock_scene(self) -> GeminiScene:
        import random
        scenarios = [
            GeminiScene(scene_summary="Mock: Path clear.", objects=[]),
            GeminiScene(scene_summary="Mock: Vehicle ahead.", objects=[
                GeminiObject("car", "danger", "center", "close", "Car directly ahead.")
            ]),
        ]
        return random.choice(scenarios)
