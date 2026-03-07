"""
config.py — Central configuration loader for the Blind Pedestrian Navigation Agent.
Reads from .env file and exposes typed constants.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ElevenLabs (voice output)
    elevenlabs_api_key: str
    elevenlabs_voice_id: str

    # Google Gemini (vision / object recognition)
    gemini_api_key: str

    # Serial / ESP32
    serial_port: str
    serial_baud: int

    # WebSocket Dashboard
    ws_port: int


def load_config() -> Config:
    return Config(
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        serial_port=os.getenv("SERIAL_PORT", "/dev/cu.usbserial-0001"),
        serial_baud=int(os.getenv("SERIAL_BAUD", "115200")),
        ws_port=int(os.getenv("WS_PORT", "8765")),
    )
