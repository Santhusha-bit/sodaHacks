"""
context_packet.py — The unified JSON data model that flows through the entire pipeline.

Every piece of information — from sensor readings, to Hume emotion scores,
to Selenium-scraped context, to the final ElevenLabs response — is collected
into a single ContextPacket. This is the "glue" between all layers.

JSON Schema (what gets serialized to the ESP32 and dashboard):
{
  "request_id": str,
  "phase": "input" | "enriching" | "responding" | "complete",
  "sensor": { "pir_triggered": bool, "uptime_ms": int },
  "user_voice": { "transcript": str, "hume_emotions": {...} },
  "scraped_context": { "traffic": [...], "weather": str, "nearby_pois": [...] },
  "response": { "text": str, "urgency": str, "haptic": str, "esp32_display": str },
  "timestamp": float
}
"""

import time
import uuid
import json
from dataclasses import dataclass, field, asdict
from typing import Optional


# ── Sub-schemas ───────────────────────────────────────────────────────────────

@dataclass
class SensorData:
    pir_triggered: bool = False
    uptime_ms: int = 0


@dataclass
class EmotionScores:
    stress: float = 0.0
    calm: float = 1.0
    fear: float = 0.0
    sadness: float = 0.0
    summary: str = "calm"


@dataclass
class UserVoice:
    """Populated by Hume EVI — transcript + emotion from driver's speech."""
    transcript: str = ""
    hume_emotions: EmotionScores = field(default_factory=EmotionScores)
    has_question: bool = False     # Did the user ask something?


@dataclass
class TrafficItem:
    severity: str        # "low" | "medium" | "high"
    description: str
    location: str
    distance_mi: float = 0.0


@dataclass
class ScrapedContext:
    """Populated by the Selenium agent — real-world environmental context."""
    traffic: list[TrafficItem] = field(default_factory=list)
    weather: str = ""
    nearby_pois: list[str] = field(default_factory=list)    # points of interest
    transit_info: list[str] = field(default_factory=list)   # bus/train nearby
    custom: dict = field(default_factory=dict)              # extensible slot


@dataclass
class AgentResponse:
    """The final synthesized response sent back to the user."""
    text: str = ""                      # Text for ElevenLabs to speak
    urgency: str = "calm"               # "calm" | "caution" | "danger"
    haptic: str = "calm"                # piezo pattern name
    esp32_display: str = "SAFE"         # text for BOX-3 LCD
    hazard_level: str = "SAFE"         # "SAFE" | "CAUTION" | "DANGER"


# ── Main packet ───────────────────────────────────────────────────────────────

@dataclass
class ContextPacket:
    """
    The single source of truth flowing through the entire pipeline.

    Lifecycle:
      1. esp32_bridge creates packet with SensorData
      2. hume_client adds UserVoice (transcript + emotion)
      3. selenium_agent adds ScrapedContext
      4. agent_core synthesizes AgentResponse
      5. Packet serialized → ESP32 + Dashboard
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    phase: str = "input"               # "input" | "enriching" | "responding" | "complete"
    sensor: SensorData = field(default_factory=SensorData)
    user_voice: UserVoice = field(default_factory=UserVoice)
    scraped_context: ScrapedContext = field(default_factory=ScrapedContext)
    response: AgentResponse = field(default_factory=AgentResponse)
    timestamp: float = field(default_factory=time.time)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_json(self, indent: int = None) -> str:
        """Serialize the full packet to a JSON string."""
        return json.dumps(asdict(self), indent=indent, default=str)

    def to_serial_payload(self) -> str:
        """
        Compact JSON line for ESP32 serial transmission.
        Only sends fields the firmware needs — keeps serial buffer short.
        """
        payload = {
            "id": self.request_id,
            "phase": self.phase,
            "haptic": self.response.haptic,
            "display": self.response.esp32_display,
            "level": self.response.hazard_level,
        }
        return json.dumps(payload) + "\n"

    def to_dashboard_state(self) -> dict:
        """Full dict for WebSocket broadcast to the caregiver dashboard."""
        return {
            "request_id": self.request_id,
            "phase": self.phase,
            "level": self.response.hazard_level,
            "haptic": self.response.haptic,
            "speech": self.response.text,
            "pir": self.sensor.pir_triggered,
            "transcript": self.user_voice.transcript,
            "emotion": {
                "stress": round(self.user_voice.hume_emotions.stress, 3),
                "calm": round(self.user_voice.hume_emotions.calm, 3),
                "fear": round(self.user_voice.hume_emotions.fear, 3),
                "summary": self.user_voice.hume_emotions.summary,
            },
            "traffic_alerts": [
                {
                    "severity": t.severity,
                    "description": t.description,
                    "location": t.location,
                }
                for t in self.scraped_context.traffic
            ],
            "weather": self.scraped_context.weather,
            "pois": self.scraped_context.nearby_pois,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_sensor_event(cls, pir_triggered: bool, uptime_ms: int = 0) -> "ContextPacket":
        """Create a new packet seeded from an ESP32 sensor event."""
        return cls(
            phase="input",
            sensor=SensorData(pir_triggered=pir_triggered, uptime_ms=uptime_ms),
        )

    @classmethod
    def from_user_voice(cls, transcript: str, emotions: EmotionScores) -> "ContextPacket":
        """Create a new packet seeded from a Hume voice interaction."""
        return cls(
            phase="input",
            user_voice=UserVoice(
                transcript=transcript,
                hume_emotions=emotions,
                has_question="?" in transcript or any(
                    w in transcript.lower()
                    for w in ["what", "where", "is there", "tell me", "how", "any"]
                ),
            ),
        )
