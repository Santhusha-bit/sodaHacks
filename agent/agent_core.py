"""
agent_core.py — DigiGuide pipeline orchestrator.

Full flow per cycle:
  1. INPUT      — ESP32 sends a JPEG frame (and PIR distance for deaf mode)
  2. VISION     — Gemini Vision analyzes frame → hazard precedence array
  3. CLASSIFYING — HazardClassifier fuses vision + PIR + emotion → HazardLevel
  4. RESPONDING — Build AgentResponse (text or haptic+screen depending on mode)
  5. OUTPUT     — Blind: ElevenLabs speaks + VAPI inject
                  Deaf:  ESP32 haptic PWM + screen string
  6. DASHBOARD  — Full packet broadcast via WebSocket

User modes:
  --mode blind  → audio output via ElevenLabs TTS (+ optional VAPI live call)
  --mode deaf   → haptic via piezo PWM + screen text on ESP32 S3 Box
"""

import asyncio
import logging
import time
from typing import Optional

from agent.config import Config
from agent.context_packet import (
    ContextPacket, SensorData, UserVoice, EmotionScores,
    ScrapedContext, AgentResponse
)
from agent.esp32_bridge import SerialBridge
from agent.tts_elevenlabs import TTSClient
from agent.hume_client import HumeClient, EmotionReading
from agent.hazard_classifier import HazardClassifier, HazardLevel
from agent.gemini_client import GeminiVisionClient
from agent.vapi_client import VAPIClient
from agent.ws_server import DashboardServer

logger = logging.getLogger(__name__)

# ── Urgency mapping (HazardLevel → output params) ────────────────────────────

URGENCY_MAP = {
    HazardLevel.SAFE:    ("calm",   "calm",   "SAFE"),
    HazardLevel.CAUTION: ("pulse",  "caution", "CAUTION"),
    HazardLevel.DANGER:  ("rapid",  "danger",  "DANGER"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hazard_to_speech(hazard: dict) -> str:
    """Convert a single Gemini hazard dict to a natural spoken sentence."""
    label    = hazard.get("label", "object")
    direction = hazard.get("direction", "ahead")
    dist     = hazard.get("distance_ft", 0)
    label_clean = label.replace("_", " ")

    if dist and dist < 15:
        urgency_prefix = "Warning! "
    elif dist and dist < 30:
        urgency_prefix = "Caution — "
    else:
        urgency_prefix = ""

    return f"{urgency_prefix}{label_clean.capitalize()} {direction}, {dist} feet away."


def _hazard_to_screen(hazard: dict) -> str:
    """Format a hazard as a short screen string for the ESP32 S3 Box display."""
    label = hazard.get("label", "OBJECT").upper().replace("_", " ")
    dist  = hazard.get("distance_ft", 0)
    direction = hazard.get("direction", "AHEAD").upper()
    return f"{label} {direction} {dist}ft"


def _distance_to_pwm(distance_ft: int) -> int:
    """Map distance in feet to a piezo PWM intensity (0–255). Closer = stronger."""
    if distance_ft <= 5:
        return 255
    elif distance_ft <= 15:
        return 200
    elif distance_ft <= 30:
        return 140
    elif distance_ft <= 60:
        return 80
    else:
        return 30


def _build_response(
    packet: ContextPacket,
    level: HazardLevel,
    reasons: list[str],
    top_hazard: Optional[dict],
    mode: str,
) -> AgentResponse:
    """Build an AgentResponse appropriate to the current user mode."""
    haptic, urgency, display = URGENCY_MAP[level]

    if mode == "blind":
        # ── Blind: produce spoken text for ElevenLabs ─────────────────────────
        if top_hazard:
            text = _hazard_to_speech(top_hazard)
        elif level == HazardLevel.SAFE:
            text = "All clear. Path ahead is safe."
        else:
            text = f"Alert: {reasons[0] if reasons else 'stay alert'}."

        # Emotion-aware suffix
        emo = packet.user_voice.hume_emotions
        if emo.stress > 0.65 or emo.fear > 0.55:
            text += " Take a breath — I'm watching everything."

        return AgentResponse(
            text=text,
            urgency=urgency,
            haptic=haptic,
            esp32_display=display,
            hazard_level=level.value,
        )

    else:
        # ── Deaf: produce haptic PWM + screen string ───────────────────────────
        if top_hazard:
            screen_str = _hazard_to_screen(top_hazard)
            pwm = _distance_to_pwm(top_hazard.get("distance_ft", 100))
        else:
            screen_str = "CLEAR" if level == HazardLevel.SAFE else display
            pwm = 0 if level == HazardLevel.SAFE else 60

        return AgentResponse(
            text=screen_str,        # displayed on ESP32 S3 Box
            urgency=urgency,
            haptic=f"pwm:{pwm}",    # custom haptic command: "pwm:200"
            esp32_display=screen_str,
            hazard_level=level.value,
        )


# ── Main Agent ────────────────────────────────────────────────────────────────

class SafetyAgent:
    """
    DigiGuide orchestrator — runs the full sensor→vision→output pipeline.
    Supports two modes:
      blind: Real World → ESP32-CAM → Gemini → Python → ElevenLabs TTS → 🔊 AUDIO
      deaf:  Real World → ESP32-CAM + PIR → Gemini → Python → 〰 HAPTIC + 🖥 SCREEN
    """

    def __init__(self, config: Config, mock: bool = False, mode: str = "blind"):
        self.config = config
        self.mock   = mock
        self.mode   = mode  # "blind" or "deaf"

        # ── Hardware ──────────────────────────────────────────────────────────
        self.bridge = SerialBridge(
            config.serial_port, config.serial_baud, mock=mock
        )

        # ── AI Clients ────────────────────────────────────────────────────────
        self.gemini = GeminiVisionClient(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            mock=mock,
        )

        # Blind-only: TTS + VAPI voice
        self.tts = TTSClient(
            config.elevenlabs_api_key, config.elevenlabs_voice_id, mock=mock
        ) if mode == "blind" else None

        self.vapi = VAPIClient(
            api_key=config.vapi_api_key,
            mock=mock or not config.vapi_api_key,
        ) if mode == "blind" else None

        # Hume emotion (optional — both modes)
        self.hume = HumeClient(
            config.hume_api_key or "", config.hume_config_id,
            mock=mock or not config.hume_api_key,
        )

        # ── Core components ───────────────────────────────────────────────────
        self.classifier = HazardClassifier(
            stress_threshold=config.hume_stress_threshold
        )
        self.dashboard = DashboardServer(port=config.ws_port)

        # ── Shared state ──────────────────────────────────────────────────────
        self._last_frame:    Optional[bytes]   = None
        self._last_emotion:  EmotionScores     = EmotionScores()
        self._pir_active:    bool              = False
        self._pir_distance:  int               = 200  # feet
        self._last_pir_time: float             = 0.0
        self._running:       bool              = False
        self._last_spoken_time: float          = 0.0
        self._last_hazard:   str               = "SAFE"

        # VAPI active call ID (if any)
        self._vapi_call_id: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        logger.info("=" * 60)
        logger.info(f"  🚶 DigiGuide — MODE: {self.mode.upper()}")
        logger.info("=" * 60)

        self.bridge.connect()
        await self.bridge.start_reader()
        await self.dashboard.start()

        # Startup announcement (blind mode only)
        if self.mode == "blind" and self.tts:
            self.tts.speak(
                "DigiGuide online. I am monitoring your surroundings and will alert you to any hazards.",
                urgency="calm",
            )

        try:
            await asyncio.gather(
                self._sensor_loop(),
                self._hume_emotion_loop(),
                self._frame_poll_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self.bridge.disconnect()
            await self.dashboard.stop()
            logger.info("[Agent] Shutdown complete.")

    async def stop(self):
        self._running = False

    # ── Loop 1: PIR / Distance Events from ESP32 ──────────────────────────────

    async def _sensor_loop(self):
        """Handle ESP32 events: PIR triggers and JPEG frames."""
        logger.info(f"[Sensor] Listening for ESP32 events (mode={self.mode})")
        async for event in self.bridge.events():
            if not self._running:
                break
            if event.event_type == "motion" and event.detected:
                now = time.time()
                if now - self._last_pir_time >= self.config.pir_cooldown_seconds:
                    self._pir_active = True
                    self._pir_distance = getattr(event, "distance_ft", 20)
                    self._last_pir_time = now
                    logger.info(f"[Sensor] 🚨 PIR triggered — distance ~{self._pir_distance}ft")
                    asyncio.create_task(self._run_pipeline(trigger="pir"))
                    asyncio.create_task(self._clear_pir(3.0))
            elif event.event_type == "frame" and hasattr(event, "jpeg_bytes"):
                self._last_frame = event.jpeg_bytes

    async def _clear_pir(self, delay: float):
        await asyncio.sleep(delay)
        self._pir_active = False

    # ── Loop 2: Hume Emotion Monitoring ───────────────────────────────────────

    async def _hume_emotion_loop(self):
        """Poll Hume for stress/fear every 5 seconds (optional — falls back to neutral)."""
        logger.info("[Hume] Emotion monitoring active" if self.config.hume_api_key else "[Hume] Skipped (no API key)")
        while self._running:
            try:
                reading = self.hume.get_last_reading()
                self._last_emotion = EmotionScores(
                    stress=reading.stress,
                    calm=reading.calm,
                    fear=reading.fear,
                    sadness=getattr(reading, "sadness", 0.0),
                    summary=getattr(reading, "summary", ""),
                )
            except Exception as e:
                logger.debug(f"[Hume] Poll error (non-fatal): {e}")
            await asyncio.sleep(5)

    # ── Loop 3: Periodic Frame-Based Vision Analysis ───────────────────────────

    async def _frame_poll_loop(self):
        """
        Every 2 seconds, run Gemini vision on the latest frame.
        Acts as the primary trigger for both Blind and Deaf pipelines.
        """
        logger.info("[Vision] Frame poll loop started (2s interval)")
        while self._running:
            await asyncio.sleep(2.0)
            if self._last_frame or self.mock:
                asyncio.create_task(self._run_pipeline(trigger="frame"))

    # ── The Core Pipeline ─────────────────────────────────────────────────────

    async def _run_pipeline(self, trigger: str = "frame", transcript: str = ""):
        """
        Full vision pipeline:
          1. Run Gemini on latest JPEG frame
          2. Classify hazard level
          3. Build mode-specific response
          4. Dispatch to ElevenLabs (blind) or ESP32 haptic+screen (deaf)
          5. Broadcast to dashboard
        """
        logger.info(f"[Pipeline] ▶ Triggered by: {trigger}")

        # ── Phase 1: Gemini Vision ─────────────────────────────────────────────
        hazards = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.gemini.analyze_frame(jpeg_bytes=self._last_frame)
        )
        top_hazard = hazards[0] if hazards else None

        # ── Phase 2: Assemble ContextPacket ───────────────────────────────────
        packet = ContextPacket(phase="vision")
        packet.sensor = SensorData(
            pir_triggered=self._pir_active,
            uptime_ms=int(time.time() * 1000),
        )
        packet.user_voice = UserVoice(
            transcript=transcript,
            hume_emotions=self._last_emotion,
            has_question="?" in transcript,
        )
        packet.scraped_context = ScrapedContext()

        # ── Phase 3: Classify ─────────────────────────────────────────────────
        from agent.traffic_scraper import TrafficAlert
        assessment = self.classifier.classify(
            pir_triggered=self._pir_active,
            traffic_alerts=[],  # Selenium removed; use Gemini hazards instead
            emotion=EmotionReading(
                stress=self._last_emotion.stress,
                calm=self._last_emotion.calm,
                fear=self._last_emotion.fear,
            ),
        )

        # Override level based on Gemini if no PIR/emotion trigger
        if not self._pir_active and self._last_emotion.stress < 0.4:
            if top_hazard:
                priority = top_hazard.get("priority", 99)
                distance = top_hazard.get("distance_ft", 200)
                if distance < 15 or priority == 1:
                    assessment = type(assessment)(
                        level=HazardLevel.DANGER,
                        reasons=[f"Gemini: {top_hazard['label']} at {distance}ft"]
                    )
                elif distance < 40:
                    assessment = type(assessment)(
                        level=HazardLevel.CAUTION,
                        reasons=[f"Gemini: {top_hazard['label']} approaching"]
                    )

        # ── Phase 4: Build response ───────────────────────────────────────────
        packet.response = _build_response(
            packet, assessment.level, assessment.reasons, top_hazard, self.mode
        )
        packet.phase = "complete"

        # ── Phase 5: Dispatch ─────────────────────────────────────────────────
        await self._dispatch(packet, previous_level=self._last_hazard)
        self._last_hazard = packet.response.hazard_level

        # ── Phase 6: Dashboard broadcast ──────────────────────────────────────
        try:
            state = packet.to_dashboard_state()
            state["mode"]    = self.mode
            state["hazards"] = hazards
            await self.dashboard.broadcast(state)
        except Exception as e:
            logger.debug(f"[Dashboard] Broadcast error (non-fatal): {e}")

        logger.info(
            f"[Pipeline] ✅ {self.mode.upper()} | "
            f"{packet.response.hazard_level} | "
            f"hazards={[h['label'] for h in hazards]}"
        )

    async def _dispatch(self, packet: ContextPacket, previous_level: str):
        """Route output to ElevenLabs (blind) or ESP32 haptic+screen (deaf)."""
        level_changed = packet.response.hazard_level != previous_level
        is_danger     = packet.response.hazard_level == "DANGER"
        cooldown_ok   = time.time() - self._last_spoken_time > 8.0
        should_alert  = level_changed or is_danger or cooldown_ok

        if self.mode == "blind":
            # Always send haptic to ESP32 (light pulse for confirmation)
            self.bridge.send_haptic(packet.response.haptic)

            # Speak via ElevenLabs
            if packet.response.text and should_alert and self.tts:
                self.tts.speak(packet.response.text, urgency=packet.response.urgency)
                self._last_spoken_time = time.time()

                # Also inject into active VAPI call if one exists
                if self._vapi_call_id and self.vapi:
                    self.vapi.inject_message(self._vapi_call_id, packet.response.text)

        else:
            # Deaf mode: send haptic PWM + screen string to ESP32
            self.bridge.send_haptic(packet.response.haptic)  # "pwm:200"
            self.bridge.send_display(
                packet.response.esp32_display,
                packet.response.hazard_level,
            )
            if should_alert:
                self._last_spoken_time = time.time()
