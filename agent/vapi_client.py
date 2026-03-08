"""
vapi_client.py — VAPI voice-call client for DigiGuide.

VAPI (https://vapi.ai) is a voice AI orchestration platform that can manage
real-time phone/voice calls with custom AI assistants. For DigiGuide it
provides a richer two-way voice interface for the Blind user pipeline:
  - User speaks → VAPI assistant hears → triggers hazard context
  - DigiGuide can push status updates into the call

REST API reference: https://api.vapi.ai
"""

import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

VAPI_BASE_URL = "https://api.vapi.ai"


class VAPIClient:
    """
    Minimal VAPI REST client for DigiGuide.

    Usage:
        client = VAPIClient(api_key="...", mock=False)
        call_id = client.start_call(phone_number="+15551234567")
        client.end_call(call_id)
    """

    def __init__(self, api_key: str, mock: bool = False):
        self.api_key = api_key
        self.mock = mock or not api_key
        self._session: Optional[requests.Session] = None

        if not self.mock:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            })
            logger.info("[VAPI] Client initialized ✅")
        else:
            logger.info("[VAPI] Running in MOCK mode")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_assistants(self) -> list[dict]:
        """List all configured VAPI assistants for this account."""
        if self.mock:
            return [{"id": "mock-assistant-id", "name": "DigiGuide Mock Assistant"}]
        try:
            resp = self._session.get(f"{VAPI_BASE_URL}/assistant")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[VAPI] get_assistants failed: {e}")
            return []

    def create_assistant(self, name: str = "DigiGuide", system_prompt: str = "") -> Optional[str]:
        """
        Create a VAPI assistant configured for DigiGuide road-safety narration.
        Returns the assistant ID, or None on failure.
        """
        if self.mock:
            logger.info(f"[VAPI MOCK] Would create assistant: {name}")
            return "mock-assistant-id"

        payload = {
            "name": name,
            "model": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "systemPrompt": system_prompt or _DIGIGUIDE_SYSTEM_PROMPT,
            },
            "voice": {
                "provider": "11labs",
                "voiceId": "21m00Tcm4TlvDq8ikWAM",  # Rachel
            },
            "firstMessage": "DigiGuide online. I'm monitoring your surroundings. How can I assist?",
        }
        try:
            resp = self._session.post(f"{VAPI_BASE_URL}/assistant", json=payload)
            resp.raise_for_status()
            assistant_id = resp.json().get("id")
            logger.info(f"[VAPI] Assistant created: {assistant_id}")
            return assistant_id
        except Exception as e:
            logger.error(f"[VAPI] create_assistant failed: {e}")
            return None

    def inject_message(self, call_id: str, message: str) -> bool:
        """
        Inject a text message into an active VAPI call (agent says it aloud).
        Used to push real-time hazard alerts into a live voice session.
        """
        if self.mock:
            logger.info(f"[VAPI MOCK] Inject into call {call_id}: {message}")
            return True
        try:
            payload = {"type": "add-message", "message": {"role": "system", "content": message}}
            resp = self._session.post(f"{VAPI_BASE_URL}/call/{call_id}/inject", json=payload)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[VAPI] inject_message failed: {e}")
            return False

    def list_calls(self, limit: int = 10) -> list[dict]:
        """Return recent call records."""
        if self.mock:
            return []
        try:
            resp = self._session.get(f"{VAPI_BASE_URL}/call", params={"limit": limit})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[VAPI] list_calls failed: {e}")
            return []

    def get_status(self) -> dict:
        """Check VAPI account status — useful for verifying the key works."""
        if self.mock:
            return {"status": "mock", "api_key_valid": False}
        try:
            assistants = self.get_assistants()
            return {"status": "ok", "assistant_count": len(assistants)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}


# ── System prompt for the DigiGuide VAPI assistant ───────────────────────────

_DIGIGUIDE_SYSTEM_PROMPT = """
You are DigiGuide, a road-crossing safety assistant for blind pedestrians.
Your role is to:
1. Narrate hazards detected by the ESP32 camera (cars, cyclists, pedestrians, traffic lights).
2. Answer questions about the user's surroundings clearly and concisely.
3. Provide step-by-step crossing guidance when the user asks.
4. Alert to DANGER conditions immediately and calmly.

Keep responses SHORT (1-2 sentences for alerts, 2-3 for questions).
Use calm, clear language. Never use filler words.
Example alerts:
  - "Car approaching from your left, about 10 feet away."
  - "Crosswalk signal is green. Path ahead is clear."
  - "Stop — vehicle entering the intersection from the right."
"""
