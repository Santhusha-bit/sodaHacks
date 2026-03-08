"""
config.py — Central configuration loader for DigiGuide.
Reads from .env file and exposes typed constants.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ElevenLabs TTS
    elevenlabs_api_key: str
    elevenlabs_voice_id: str

    # Google Gemini Vision
    gemini_api_key: str
    gemini_model: str

    # VAPI (Voice AI Platform)
    vapi_api_key: str

    # Hume AI (optional — emotion detection)
    hume_api_key: str | None
    hume_config_id: str | None

    # Serial / ESP32
    serial_port: str
    serial_baud: int

    # Agent Behaviour
    user_mode: str          # "blind" or "deaf"
    pir_cooldown_seconds: int
    hume_stress_threshold: float

    # WebSocket Dashboard
    ws_port: int


def load_config() -> Config:
    return Config(
        # ElevenLabs
        elevenlabs_api_key=_require("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),

        # Gemini
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),

        # VAPI
        vapi_api_key=os.getenv("VAPI_API_KEY", ""),

        # Hume (optional — graceful fallback to neutral emotions if not set)
        hume_api_key=os.getenv("HUME_API_KEY") or None,
        hume_config_id=os.getenv("HUME_CONFIG_ID") or None,

        # Serial
        serial_port=os.getenv("SERIAL_PORT", "/dev/cu.usbserial-0001"),
        serial_baud=int(os.getenv("SERIAL_BAUD", "115200")),

        # Behaviour
        user_mode=os.getenv("USER_MODE", "blind").lower(),
        pir_cooldown_seconds=int(os.getenv("PIR_COOLDOWN_SECONDS", "5")),
        hume_stress_threshold=float(os.getenv("HUME_STRESS_THRESHOLD", "0.65")),

        # WebSocket
        ws_port=int(os.getenv("WS_PORT", "8765")),
    )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in your API keys."
        )
    return value
