"""
pedestrian_agent.py — Blind Pedestrian Navigation Agent orchestrator.

Fuses three real-time data streams:
  1. Distance sensor (ESP32 HC-SR04) → proximity of objects in cm
  2. Vision (YOLOv8 on phone camera) → vehicles, signals, people
  3. Hume EVI → pedestrian stress/fear level

And produces natural voice alerts via ElevenLabs + haptic feedback via ESP32:
  • "Walk signal is on. Path clear. Safe to cross."
  • "Car approaching, 4 feet ahead. Do not cross."
  • "Red light. Wait at the curb."
  • "Person crossing to your left, 8 feet away."
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
from agent.vision_detector import VisionDetector, SceneAnalysis
from agent.camera_bridge import CameraBridge, PhoneCameraConfig
from agent.ws_server import DashboardServer

logger = logging.getLogger(__name__)


# ── Distance zone from centimetres ────────────────────────────────────────────

def _cm_to_zone(cm: float) -> tuple[str, str, str]:
    """Returns (zone_name, urgency, speech_distance)."""
    if cm <= 60:    return "immediate", "danger",  f"{cm:.0f} centimeters"
    if cm <= 150:   return "near",      "caution", f"{cm / 30.48:.1f} feet"
    if cm <= 300:   return "medium",    "caution", f"{cm / 30.48:.0f} feet"
    return           "far",      "calm",    f"{cm / 30.48:.0f} feet"


# ── Alert rate-limiter ────────────────────────────────────────────────────────

class AlertThrottle:
    """Prevent the same message being repeated within a cooldown window."""

    def __init__(self, cooldown_s: float = 6.0):
        self._cooldown = cooldown_s
        self._last_msgs: dict[str, float] = {}

    def should_speak(self, key: str) -> bool:
        now = time.time()
        last = self._last_msgs.get(key, 0.0)
        if now - last >= self._cooldown:
            self._last_msgs[key] = now
            return True
        return False


# ── Main Agent ────────────────────────────────────────────────────────────────

class PedestrianAgent:
    """
    Blind Pedestrian Navigation Agent.
    Integrates: ESP32 distance sensor + phone camera vision + Hume emotion
    → ElevenLabs voice alerts + ESP32 haptic feedback.
    """

    def __init__(self, config: Config, mock: bool = False):
        self.config = config
        self.mock   = mock

        hw_mock = mock
        tts_mock = not bool(config.elevenlabs_api_key)

        if mock and not tts_mock:
            import logging
            logging.getLogger(__name__).info(
                "[Agent] --mock mode: hardware simulated, ElevenLabs TTS is REAL (key found)")

        # Subsystems
        self.bridge    = SerialBridge(config.serial_port, config.serial_baud, mock=hw_mock)
        self.tts       = TTSClient(config.elevenlabs_api_key, config.elevenlabs_voice_id, mock=tts_mock)
        self.camera    = CameraBridge(
            stream_url=PhoneCameraConfig.from_env(),
            fps_limit=5,
            mock=hw_mock,
        )
        self.vision    = VisionDetector(mock=hw_mock)
        self.dashboard = DashboardServer(port=config.ws_port)
        self.throttle  = AlertThrottle(cooldown_s=6.0)

        # Live state
        self._distance_cm: Optional[float] = None
        self._scene: SceneAnalysis = SceneAnalysis()
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True

        print()
        print("╔══════════════════════════════════════════════════════╗")
        print("║  👁  Blind Pedestrian Navigation Agent               ║")
        print("║      Camera + Distance + Hume + ElevenLabs           ║")
        print("╚══════════════════════════════════════════════════════╝")
        print()

        self.bridge.connect()
        await self.bridge.start_reader()
        await self.dashboard.start()

        # Register vision callback — runs on every camera frame
        self.camera.add_frame_callback(self._on_frame)
        self.camera.start()

        self.tts.speak(
            "Pedestrian navigation agent is active. "
            "Point the camera at the road ahead. I will guide you.",
            urgency="calm",
        )

        try:
            await asyncio.gather(
                self._distance_loop(),
                self._assessment_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self.camera.stop()
            self.bridge.disconnect()
            await self.dashboard.stop()
            logger.info("[Agent] Shutdown complete.")

    async def stop(self):
        self._running = False

    # ── Camera callback (runs in camera thread) ───────────────────────────────

    def _on_frame(self, frame):
        """Called by CameraBridge on each video frame. Runs in camera thread."""
        try:
            scene = self.vision.analyze_frame(frame)
            self._scene = scene
        except Exception as e:
            logger.error(f"[Vision] Frame processing error: {e}")

    # ── Distance sensor loop ──────────────────────────────────────────────────

    async def _distance_loop(self):
        """Parse distance events from ESP32 serial stream."""
        logger.info("[Distance] Listening for HC-SR04 readings from ESP32")
        async for event in self.bridge.events():
            if not self._running:
                break
            if event.event_type == "distance" and event.raw:
                cm = event.raw.get("cm", -1)
                if cm > 0:
                    self._distance_cm = float(cm)

    # ── Main assessment & alert loop ──────────────────────────────────────────

    async def _assessment_loop(self):
        """Every 1.5s: fuse all data → decide alerts → dispatch."""
        logger.info("[Agent] Assessment loop started")

        while self._running:
            await asyncio.sleep(1.5)
            try:
                await self._assess_and_respond()
            except Exception as e:
                logger.error(f"[Agent] Assessment error: {e}")

    async def _assess_and_respond(self):
        scene   = self._scene
        dist_cm = self._distance_cm

        alerts: list[tuple[str, str, str]] = []  # (key, speech, urgency)

        # ── Priority 1: Immediate distance danger ──────────────────────────
        if dist_cm is not None and dist_cm <= 80:
            zone, urg, dist_str = _cm_to_zone(dist_cm)
            alerts.append((
                f"dist_{zone}",
                f"Warning! Object {dist_str} ahead. Stop.",
                "danger",
            ))

        # ── Priority 2: Vision — crossing decision ─────────────────────────
        if scene.walk_signal is True and not scene.vehicles_nearby:
            alerts.append((
                "walk_signal_safe",
                "Walk signal is on. Path is clear. Safe to cross now.",
                "calm",
            ))
        elif scene.walk_signal is False:
            alerts.append((
                "dont_walk",
                "Don't walk signal is active. Wait at the curb.",
                "caution",
            ))
        elif scene.traffic_light_color == "red":
            alerts.append(("red_light", "Red light. Do not cross.", "caution"))
        elif scene.traffic_light_color == "green":
            if scene.vehicles_nearby:
                closest = scene.vehicles_nearby[0]
                alerts.append((
                    f"vehicle_{closest.distance_zone}",
                    f"{closest.to_speech()} Do not cross yet.",
                    "danger",
                ))

        # ── Priority 3: Nearby vehicles from vision ────────────────────────
        for det in scene.vehicles_nearby:
            key = f"vehicle_{det.h_position}_{det.distance_zone}"
            alerts.append((key, det.to_speech(), det.urgency))

        # ── Dispatch top-priority alert ────────────────────────────────────
        for key, speech, urgency in alerts:
            if self.throttle.should_speak(key):
                self._dispatch(speech, urgency)
                await self._push_dashboard(speech, urgency, scene, dist_cm)
                logger.info(f"[Alert] [{urgency.upper()}] {speech}")
                break  # one alert per cycle

        # Push dashboard even without an alert
        if not alerts:
            await self._push_dashboard("All clear.", "calm", scene, dist_cm)

    # ── Output dispatch ───────────────────────────────────────────────────────

    def _dispatch(self, speech: str, urgency: str):
        haptic_map = {"danger": "rapid", "caution": "pulse", "calm": "long"}
        haptic = haptic_map.get(urgency, "calm")
        self.bridge.send_haptic(haptic)
        level = urgency.upper()
        self.bridge.send_display(str(speech)[:24], level)   # LCD fits ~24 chars
        self.tts.speak(speech, urgency=urgency)

    async def _push_dashboard(
        self,
        speech: str,
        urgency: str,
        scene: SceneAnalysis,
        dist_cm: Optional[float],
    ):
        state = {
            "level": urgency.upper(),
            "speech": speech,
            "distance_cm": float(f"{dist_cm:.1f}") if dist_cm is not None else None,
            "safe_to_cross": scene.safe_to_cross,
            "traffic_light": scene.traffic_light_color,
            "walk_signal": scene.walk_signal,
            "detections": [
                {
                    "label": d.label,
                    "distance_zone": d.distance_zone,
                    "position": d.h_position,
                    "urgency": d.urgency,
                    "confidence": round(d.confidence, 2),
                }
                for d in scene.detections[:6]     # top 6
            ],
            "emotion": None,
            "timestamp": time.time(),
        }
        await self.dashboard.broadcast(state)
