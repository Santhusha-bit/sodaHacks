"""
agent_core.py — Pipeline-based orchestration using ContextPacket as the data spine.

Full flow per interaction cycle:
  1. INPUT      — ESP32 sensor event OR user voice (Hume EVI) creates a ContextPacket
  2. ENRICHING  — Selenium agent fetches real-world context (traffic, weather, POIs)
  3. RESPONDING — HazardClassifier synthesizes AgentResponse
  4. OUTPUT     — ElevenLabs speaks response, ESP32 gets haptic+display command
  5. COMPLETE   — Dashboard receives full packet via WebSocket
"""

import asyncio
import logging
import time
from typing import Optional

from agent.config import Config
from agent.context_packet import (
    ContextPacket, SensorData, UserVoice, EmotionScores,
    ScrapedContext, TrafficItem, AgentResponse
)
from agent.esp32_bridge import SerialBridge
from agent.tts_elevenlabs import TTSClient
from agent.hume_client import HumeClient, EmotionReading
from agent.traffic_scraper import TrafficScraper, TrafficAlert
from agent.hazard_classifier import HazardClassifier, HazardLevel
from agent.ws_server import DashboardServer

logger = logging.getLogger(__name__)

# ── Response builder: Hume + Selenium → AgentResponse ────────────────────────

URGENCY_MAP = {
    HazardLevel.SAFE: ("calm", "calm", "SAFE"),
    HazardLevel.CAUTION: ("pulse", "caution", "CAUTION"),
    HazardLevel.DANGER: ("rapid", "danger", "DANGER"),
}


def _build_response(packet: ContextPacket, level: HazardLevel, reasons: list[str]) -> AgentResponse:
    """Synthesize a natural response from packet data."""
    haptic, urgency, display = URGENCY_MAP[level]

    # Build a rich contextual reply for ElevenLabs to speak
    parts = []

    if packet.user_voice.transcript and packet.user_voice.has_question:
        # User asked something — answer it directly first
        ctx = packet.scraped_context
        if ctx.weather:
            parts.append(f"Current weather: {ctx.weather}.")
        if ctx.nearby_pois:
            parts.append(f"Nearby: {', '.join(ctx.nearby_pois[:3])}.")
        if ctx.transit_info:
            parts.append(ctx.transit_info[0])

    # Add hazard alerts
    if level == HazardLevel.DANGER:
        traffic = packet.scraped_context.traffic
        if traffic:
            parts.append(f"Warning. {traffic[0].description} near {traffic[0].location}.")
        if packet.sensor.pir_triggered:
            parts.append("Caution — object or person detected immediately ahead.")

    elif level == HazardLevel.CAUTION:
        if packet.sensor.pir_triggered:
            parts.append("Heads up — motion detected nearby. Stay aware.")
        traffic = packet.scraped_context.traffic
        if traffic:
            parts.append(f"Traffic advisory near {traffic[0].location}.")

    # Emotion-aware suffix
    emo = packet.user_voice.hume_emotions
    if emo.stress > 0.65 or emo.fear > 0.55:
        parts.append("I can sense you're tense — take a breath. I'm monitoring everything.")

    text = " ".join(parts) if parts else (
        "All clear. Roads are safe and no hazards detected." if level == HazardLevel.SAFE else
        f"Alert: {level.value.lower()} conditions. {reasons[0] if reasons else 'stay alert'}."
    )

    return AgentResponse(
        text=text,
        urgency=urgency,
        haptic=haptic,
        esp32_display=display,
        hazard_level=level.value,
    )


# ── Main Agent ────────────────────────────────────────────────────────────────

class SafetyAgent:
    """
    Orchestrates the full ContextPacket pipeline:
      ESP32 → Hume → Selenium → ElevenLabs → ESP32 + Dashboard
    """

    def __init__(self, config: Config, mock: bool = False):
        self.config = config
        self.mock = mock

        self.bridge     = SerialBridge(config.serial_port, config.serial_baud, mock=mock)
        self.tts        = TTSClient(config.elevenlabs_api_key, config.elevenlabs_voice_id, mock=mock)
        self.hume       = HumeClient(config.hume_api_key, config.hume_config_id, mock=mock)
        self.scraper    = TrafficScraper(config.scrape_location, mock=mock)
        self.classifier = HazardClassifier(stress_threshold=config.hume_stress_threshold)
        self.dashboard  = DashboardServer(port=config.ws_port)

        # Shared enriched state (refreshed by background loops)
        self._scraped_context: ScrapedContext = ScrapedContext()
        self._last_emotion: EmotionScores = EmotionScores()
        self._pir_active: bool = False
        self._last_pir_time: float = 0.0
        self._running: bool = False

        # Prevent alert flooding
        self._last_spoken_time: float = 0.0
        self._last_hazard: str = "SAFE"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        logger.info("="*60)
        logger.info("  🚗  Blind Driver Safety Agent — Pipeline Active")
        logger.info("="*60)

        self.bridge.connect()
        await self.bridge.start_reader()
        await self.dashboard.start()

        self.tts.speak(
            "Safety agent online. I am monitoring your surroundings and will alert you to any hazards.",
            urgency="calm",
        )

        try:
            await asyncio.gather(
                self._sensor_loop(),
                self._traffic_enrichment_loop(),
                self._hume_emotion_loop(),
                self._assessment_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self.bridge.disconnect()
            await self.dashboard.stop()
            logger.info("[Agent] Shutdown complete.")

    async def stop(self):
        self._running = False

    # ── Loop 1: Sensor Events (ESP32 → ContextPacket) ─────────────────────────

    async def _sensor_loop(self):
        """PIR events from ESP32 immediately trigger pipeline processing."""
        logger.info("[Sensor] Listening for PIR events from ESP32-S3-BOX-3")
        async for event in self.bridge.events():
            if not self._running:
                break
            if event.event_type == "motion" and event.detected:
                now = time.time()
                if now - self._last_pir_time >= self.config.pir_cooldown_seconds:
                    self._pir_active = True
                    self._last_pir_time = now
                    logger.info("[Sensor] 🚨 PIR triggered — immediate pipeline dispatch")
                    asyncio.create_task(self._run_pipeline(trigger="pir"))
                    asyncio.create_task(self._clear_pir(3.0))

    async def _clear_pir(self, delay: float):
        await asyncio.sleep(delay)
        self._pir_active = False

    # ── Loop 2: Selenium Context Enrichment (background refresh) ──────────────

    async def _traffic_enrichment_loop(self):
        """
        Selenium scrapes traffic, weather, and POIs periodically.
        Results stored as ScrapedContext and included in every packet.
        """
        logger.info(f"[Selenium] Context enrichment every {self.config.traffic_poll_interval}s")
        while self._running:
            try:
                alerts = await asyncio.get_event_loop().run_in_executor(
                    None, self.scraper.get_alerts
                )
                # Convert to TrafficItem objects
                traffic_items = [
                    TrafficItem(
                        severity=a.severity,
                        description=a.description,
                        location=a.location,
                    )
                    for a in alerts
                ]
                self._scraped_context = ScrapedContext(
                    traffic=traffic_items,
                    weather=self._scraped_context.weather,   # retained between scrapes
                    nearby_pois=self._scraped_context.nearby_pois,
                )
                logger.info(f"[Selenium] Context refreshed: {len(traffic_items)} traffic items")
            except Exception as e:
                logger.error(f"[Selenium] Enrichment error: {e}")

            await asyncio.sleep(self.config.traffic_poll_interval)

    # ── Loop 3: Hume Emotion Monitoring ───────────────────────────────────────

    async def _hume_emotion_loop(self):
        """
        Polls Hume for driver emotion state continuously.
        In a full deployment, this would open a live WebSocket stream
        from the ESP32-S3-BOX's mic to Hume EVI.
        """
        logger.info("[Hume] Driver emotion monitoring active")
        while self._running:
            try:
                reading = self.hume.get_last_reading()
                self._last_emotion = EmotionScores(
                    stress=reading.stress,
                    calm=reading.calm,
                    fear=reading.fear,
                    sadness=reading.sadness,
                    summary=reading.summary,
                )
            except Exception as e:
                logger.error(f"[Hume] Emotion poll error: {e}")
            await asyncio.sleep(5)

    # ── Loop 4: Periodic Assessment ───────────────────────────────────────────

    async def _assessment_loop(self):
        """Run pipeline every 2s even without sensor trigger (for traffic/emotion changes)."""
        logger.info("[Agent] Periodic assessment loop started")
        while self._running:
            await asyncio.sleep(2.0)
            # Only run if something noteworthy changed
            has_traffic = bool(self._scraped_context.traffic)
            is_stressed = self._last_emotion.stress > self.config.hume_stress_threshold
            if has_traffic or is_stressed:
                await self._run_pipeline(trigger="periodic")

    # ── The Core Pipeline ─────────────────────────────────────────────────────

    async def _run_pipeline(self, trigger: str = "periodic", transcript: str = ""):
        """
        Full ContextPacket pipeline:
          1. Build packet from current state
          2. Classify hazard level (Hume + sensor + traffic fused)
          3. Build AgentResponse
          4. Dispatch: haptic + TTS + ESP32 display + dashboard
        """
        logger.info(f"[Pipeline] ▶ Triggered by: {trigger}")

        # ── Phase 1: INPUT — assemble packet ──────────────────────────────────
        packet = ContextPacket(phase="input")
        packet.sensor = SensorData(
            pir_triggered=self._pir_active,
            uptime_ms=int(time.time() * 1000),
        )
        packet.user_voice = UserVoice(
            transcript=transcript,
            hume_emotions=self._last_emotion,
            has_question="?" in transcript,
        )

        # ── Phase 2: ENRICHING — attach scraped context ───────────────────────
        packet.phase = "enriching"
        packet.scraped_context = self._scraped_context
        logger.info(f"[Pipeline] Context: {len(packet.scraped_context.traffic)} traffic, "
                    f"weather='{packet.scraped_context.weather}'")

        # ── Phase 3: CLASSIFY → build response ───────────────────────────────
        # Convert to types hazard_classifier expects (already imported at top)
        traffic_alerts = [
            TrafficAlert(t.severity, t.description, t.location, "pipeline")
            for t in packet.scraped_context.traffic
        ]
        emotion_reading = EmotionReading(
            stress=packet.user_voice.hume_emotions.stress,
            calm=packet.user_voice.hume_emotions.calm,
            fear=packet.user_voice.hume_emotions.fear,
        )

        assessment = self.classifier.classify(
            pir_triggered=packet.sensor.pir_triggered,
            traffic_alerts=traffic_alerts,
            emotion=emotion_reading,
        )

        packet.response = _build_response(packet, assessment.level, assessment.reasons)
        packet.phase = "complete"

        # ── Phase 4: OUTPUT ───────────────────────────────────────────────────
        await self._dispatch(packet, previous_level=self._last_hazard)
        self._last_hazard = packet.response.hazard_level

        # ── Phase 5: Broadcast full packet to dashboard ───────────────────────
        await self.dashboard.broadcast(packet.to_dashboard_state())

        logger.info(f"[Pipeline] ✅ [{packet.request_id}] "
                    f"{packet.response.hazard_level} | haptic={packet.response.haptic}")

    async def _dispatch(self, packet: ContextPacket, previous_level: str):
        """Send haptic + display to ESP32, speak via ElevenLabs."""
        # 1. Send compact JSON to ESP32 over serial
        self.bridge.send_haptic(packet.response.haptic)
        self.bridge.send_display(packet.response.esp32_display, packet.response.hazard_level)

        # 2. Speak — only if level changed or DANGER (avoid repetition)
        level_changed = packet.response.hazard_level != previous_level
        is_danger = packet.response.hazard_level == "DANGER"
        cooldown_ok = time.time() - self._last_spoken_time > 8.0

        if packet.response.text and (level_changed or is_danger or cooldown_ok):
            self.tts.speak(packet.response.text, urgency=packet.response.urgency)
            self._last_spoken_time = time.time()
