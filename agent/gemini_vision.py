"""
gemini_vision.py — Google Gemini Vision for blind pedestrian object recognition.

Uses the new google-genai SDK with Gemini 2.0 Flash (fast, multimodal).

Sends a camera frame to Gemini with a pedestrian-specific prompt.
Gemini returns a structured JSON scene description:
  - list of detected objects with urgency + distance hint
  - traffic light color
  - walk/don't-walk signal state
  - overall crossing recommendation

This gives natural language descriptions like:
  "red sedan approaching from the left, very close"
  "walk signal is ON — safe to cross"
"""

import base64
import json
import logging
import time
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
- traffic_light: "red" | "green" | "yellow" | null
- walk_signal: true (walk signal visible ON) | false (don't walk) | null (not visible)
- safe_to_cross: true only if green/walk AND no vehicles close
- Only include objects relevant to pedestrian safety
- speech must be a clear, brief sentence for a blind person
- List danger objects first
- If scene is clear, objects can be empty and safe_to_cross true
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
    """
    Calls Gemini 2.0 Flash with a camera frame and returns a GeminiScene.
    Uses the new google-genai SDK (not the deprecated google-generativeai).
    Mock mode returns rotating scenarios without making API calls.
    """

    def __init__(self, api_key: str, mock: bool = False):
        # Treat placeholder values as missing
        real_key = bool(api_key) and not api_key.startswith("your_")
        self.mock = mock or not real_key
        self._client = None

        if not self.mock:
            self._init_client(api_key)

    def _init_client(self, api_key: str):
        try:
            from google import genai
            self._client = genai.Client(api_key=api_key)
            logger.info("[Gemini] ✅ Gemini 2.0 Flash ready (google-genai SDK)")
        except ImportError:
            logger.error(
                "[Gemini] google-genai not installed. Run: pip install google-genai"
            )
            self.mock = True
        except Exception as e:
            logger.error(f"[Gemini] Init failed: {e}")
            self.mock = True

    def analyze_frame(self, frame) -> GeminiScene:
        """Analyze a BGR numpy frame from OpenCV. Returns GeminiScene."""
        if self.mock:
            return self._mock_scene()
        try:
            return self._call_gemini(frame)
        except Exception as e:
            logger.error(f"[Gemini] API error: {e}")
            return GeminiScene(error=str(e), scene_summary="Vision unavailable.")

    def _call_gemini(self, frame) -> GeminiScene:
        import cv2
        from google import genai
        from google.genai import types

        # Encode frame as JPEG
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        img_bytes = bytes(buf)

        response = self._client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                PEDESTRIAN_PROMPT,
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=512,
            ),
        )

        raw = response.text.strip() if response.text else ""
        return self._parse(raw)

    def _parse(self, raw: str) -> GeminiScene:
        cleaned = raw.strip()
        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"[Gemini] JSON parse failed: {e}\nRaw: {raw[:200]}")
            return GeminiScene(error=f"parse error: {e}", raw_response=raw)

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

    # ── Mock scenarios (used when no API key) ─────────────────────────────────

    def _mock_scene(self) -> GeminiScene:
        import random
        scenarios = [
            GeminiScene(
                safe_to_cross=True, traffic_light="green", walk_signal=True,
                scene_summary="Walk signal is on. Path is clear.",
                objects=[],
            ),
            GeminiScene(
                safe_to_cross=False, traffic_light="red", walk_signal=False,
                scene_summary="Red light. Car approaching.",
                objects=[GeminiObject(
                    label="car", urgency="danger", position="center",
                    distance_hint="close",
                    speech="Car directly ahead, about 8 feet away.",
                )],
            ),
            GeminiScene(
                safe_to_cross=False, traffic_light="red", walk_signal=False,
                scene_summary="Red light. Wait at curb.",
                objects=[],
            ),
            GeminiScene(
                safe_to_cross=False, traffic_light=None, walk_signal=None,
                scene_summary="Cyclist approaching from the left.",
                objects=[GeminiObject(
                    label="bicycle", urgency="caution", position="left",
                    distance_hint="moderate",
                    speech="Cyclist to your left, about 15 feet away.",
                )],
            ),
        ]
        scene = random.choice(scenarios)
        logger.info(f"[MockGemini] 🎭 {scene.scene_summary}")
        return scene
