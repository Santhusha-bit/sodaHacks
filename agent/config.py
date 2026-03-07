"""
config.py — Central configuration loader for the Blind Driver Safety Agent.
Reads from .env file and exposes typed constants.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ElevenLabs
    elevenlabs_api_key: str
    elevenlabs_voice_id: str

    # Hume
    hume_api_key: str
    hume_config_id: str | None

    # Serial / ESP32
    serial_port: str
    serial_baud: int

    # Traffic Scraper
    scrape_location: str
    traffic_poll_interval: int

    # Agent Behaviour
    pir_cooldown_seconds: int
    hume_stress_threshold: float

    # WebSocket Dashboard
    ws_port: int


def load_config() -> Config:
    return Config(
        elevenlabs_api_key=_require("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        hume_api_key=_require("HUME_API_KEY"),
        hume_config_id=os.getenv("HUME_CONFIG_ID") or None,
        serial_port=os.getenv("SERIAL_PORT", "/dev/cu.usbserial-0001"),
        serial_baud=int(os.getenv("SERIAL_BAUD", "115200")),
        scrape_location=os.getenv("SCRAPE_LOCATION", "San Francisco, CA"),
        traffic_poll_interval=int(os.getenv("TRAFFIC_POLL_INTERVAL", "120")),
        pir_cooldown_seconds=int(os.getenv("PIR_COOLDOWN_SECONDS", "5")),
        hume_stress_threshold=float(os.getenv("HUME_STRESS_THRESHOLD", "0.65")),
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
