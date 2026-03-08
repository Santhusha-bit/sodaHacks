"""
gemini_client.py — Gemini Vision API wrapper for DigiGuide.

Sends a JPEG frame to Gemini 2.0 Flash and returns a structured
precedence array of detected hazards, sorted by priority (1 = most urgent).

Precedence array schema:
  [
    {"label": "car", "direction": "left", "distance_ft": 10, "priority": 1},
    {"label": "cyclist", "direction": "ahead", "distance_ft": 25, "priority": 2},
  ]

Usage:
  client = GeminiVisionClient(api_key="...", mock=False)
  hazards = client.analyze_frame(jpeg_bytes)  # returns list[dict]

SDK: google-genai (https://pypi.org/project/google-genai/)
"""

import json
import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Mock data: realistic hazard scenarios for development/demo ─────────────────

_MOCK_SCENARIOS = [
    [],  # all clear
    [],  # all clear
    [
        {"label": "pedestrian", "direction": "ahead", "distance_ft": 20, "priority": 1},
    ],
    [
        {"label": "car", "direction": "left", "distance_ft": 10, "priority": 1},
        {"label": "cyclist", "direction": "ahead", "distance_ft": 30, "priority": 2},
    ],
    [
        {"label": "car", "direction": "right", "distance_ft": 6, "priority": 1},
    ],
    [
        {"label": "traffic_light_red", "direction": "ahead", "distance_ft": 15, "priority": 1},
        {"label": "car", "direction": "left", "distance_ft": 25, "priority": 2},
    ],
]

# ── Prompt sent to Gemini ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """
You are a road-safety vision system integrated into a wearable device for
disabled pedestrians. Analyze the provided image and return ONLY a JSON array
of detected hazards, sorted ascending by priority (1 = most urgent).

Each object in the array must have exactly these fields:
  - label       (string): one of "car", "truck", "bus", "cyclist", "pedestrian",
                          "traffic_light_red", "traffic_light_green", "dog", "other"
  - direction   (string): "left", "right", "ahead", "behind"
  - distance_ft (number): estimated distance in feet (integer, 1–200)
  - priority    (number): urgency rank starting at 1. Lower = more dangerous.
                          Rank by: moving vehicle < stationary vehicle < person < other

If no hazards are present, return an empty array: []

Return ONLY the JSON array. No explanation, no markdown, no code fences.
"""


class GeminiVisionClient:
    """
    Wraps the Gemini Vision API to perform hazard detection on JPEG frames.

    Args:
        api_key: Google AI Studio API key (or empty string in mock mode).
        mock:    If True, return synthetic hazard data without API calls.
        model:   Gemini model name. Default: gemini-2.0-flash-exp
    """

    def __init__(
        self,
        api_key: str = "",
        mock: bool = False,
        model: str = "gemini-2.0-flash",
    ):
        self.mock  = mock
        self.model = model
        self._client = None
        self._scenario_index = 0

        if not mock:
            try:
                from google import genai
                from google.genai import types as genai_types
                self._genai_types = genai_types
                client = genai.Client(api_key=api_key)
                self._client = client
                logger.info(f"[Gemini] Client initialized — model: {model}")
            except ImportError:
                logger.error(
                    "[Gemini] google-genai not installed. "
                    "Run: pip install google-genai"
                )
                self.mock = True
            except Exception as e:
                logger.error(f"[Gemini] Init error: {e} — falling back to mock")
                self.mock = True

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze_frame(
        self,
        jpeg_bytes: Optional[bytes] = None,
        jpeg_path:  Optional[str]   = None,
    ) -> list[dict]:
        """
        Analyze a JPEG frame and return a sorted hazard precedence array.

        Provide either jpeg_bytes (raw bytes) OR jpeg_path (file path).
        Returns [] if the frame is clear or on any error.
        """
        if self.mock:
            return self._mock_response()

        if jpeg_bytes is None and jpeg_path is not None:
            jpeg_bytes = Path(jpeg_path).read_bytes()

        if not jpeg_bytes:
            logger.warning("[Gemini] analyze_frame called with no image data")
            return []

        try:
            return self._call_api(jpeg_bytes)
        except Exception as e:
            logger.error(f"[Gemini] API call failed: {e}")
            return []

    # ── Internal ───────────────────────────────────────────────────────────────

    def _call_api(self, jpeg_bytes: bytes) -> list[dict]:
        from google.genai import types as t
        response = self._client.models.generate_content(
            model=self.model,
            contents=[
                _SYSTEM_PROMPT,
                t.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
            ],
            config=t.GenerateContentConfig(temperature=0.1, max_output_tokens=512),
        )
        raw = response.text.strip()
        logger.debug(f"[Gemini] Raw response: {raw[:200]}")

        hazards = json.loads(raw)
        if not isinstance(hazards, list):
            logger.warning("[Gemini] Unexpected response shape — returning []")
            return []

        return self._validate_and_sort(hazards)

    def _validate_and_sort(self, hazards: list) -> list[dict]:
        """Validate fields and sort by priority ascending."""
        valid = []
        for h in hazards:
            if not isinstance(h, dict):
                continue
            entry = {
                "label":       str(h.get("label",       "other")),
                "direction":   str(h.get("direction",   "ahead")),
                "distance_ft": int(h.get("distance_ft", 50)),
                "priority":    int(h.get("priority",    99)),
            }
            valid.append(entry)

        valid.sort(key=lambda x: x["priority"])
        logger.info(f"[Gemini] Detected {len(valid)} hazard(s): "
                    + ", ".join(f"{h['label']}@{h['distance_ft']}ft" for h in valid))
        return valid

    def _mock_response(self) -> list[dict]:
        """Cycle through mock scenarios deterministically for demo/testing."""
        scenario = _MOCK_SCENARIOS[self._scenario_index % len(_MOCK_SCENARIOS)]
        self._scenario_index += 1
        if scenario:
            logger.info(f"[Gemini MOCK] Scenario: {[h['label'] for h in scenario]}")
        else:
            logger.info("[Gemini MOCK] Scene clear")
        return list(scenario)  # return a copy

    def top_hazard(self, frame: Optional[bytes] = None) -> Optional[dict]:
        """Convenience: return the single highest-priority hazard, or None."""
        hazards = self.analyze_frame(jpeg_bytes=frame)
        return hazards[0] if hazards else None
