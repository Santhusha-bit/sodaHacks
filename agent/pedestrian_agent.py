"""
pedestrian_agent.py — Blind Pedestrian Navigation Agent (Gemini + ElevenLabs pipeline).

Pipeline:
  1. Phone camera frame  →  GeminiVision (Gemini 1.5 Flash)
                            → natural language scene description (JSON)
  2. HC-SR04 distance    →  exact proximity in cm
  3. Fuse both           →  build_alert_queue() → priority-sorted alerts[]
  4. ElevenLabs          →  reads alerts[0] (highest priority) aloud
  5. ESP32 haptic        →  vibrates with urgency-matched pattern
  6. Dashboard           →  broadcasts full state via WebSocket

Alert priority order (always read most urgent first):
  1. DANGER   — sensor ≤60cm  OR  Gemini danger object
  2. CAUTION  — red light / don't walk / caution object
  3. CALM     — safe to cross / path clear
"""

import asyncio
import logging
import time
from typing import Optional

from agent.config import Config
from agent.esp32_bridge import SerialBridge
from agent.tts_elevenlabs import TTSClient
from agent.gemini_vision import GeminiVision, GeminiScene
from agent.alert_queue import build_alert_queue
from agent.camera_bridge import CameraBridge, PhoneCameraConfig
from agent.ws_server import DashboardServer
from agent.camera_server import CameraStreamServer, update_frame as cam_update

logger = logging.getLogger(__name__)


# ── Alert throttle (prevent repeating same alert too often) ───────────────────

class AlertThrottle:
    def __init__(self, cooldown_s: float = 6.0):
        self._cooldown = cooldown_s
        self._last: dict[str, float] = {}

    def should_speak(self, key: str) -> bool:
        now = time.time()
        if now - self._last.get(key, 0.0) >= self._cooldown:
            self._last[key] = now
            return True
        return False

    def key_for(self, alert: dict) -> str:
        """Stable key for deduplication — based on urgency + first 40 chars."""
        return f"{alert['urgency']}:{alert['text'][:40]}"


# ── Main Agent ────────────────────────────────────────────────────────────────

class PedestrianAgent:
    """
    Orchestrates the full pedestrian navigation pipeline.
    Gemini Vision → Priority Queue → ElevenLabs Voice + ESP32 Haptic.
    """

    def __init__(self, config: Config, mock: bool = False):
        self.config = config

        # Hardware mock: simulates ESP32 serial + camera
        hw_mock  = mock
        # API mock: only if key is missing
        tts_mock    = not bool(config.elevenlabs_api_key)
        gemini_mock = not bool(config.gemini_api_key)

        if mock:
            if not tts_mock:
                logger.info("[Agent] --mock: hardware simulated, ElevenLabs is REAL ✅")
            if not gemini_mock:
                logger.info("[Agent] --mock: hardware simulated, Gemini Vision is REAL ✅")

        # Subsystems
        self.bridge    = SerialBridge(config.serial_port, config.serial_baud, mock=hw_mock)
        self.tts       = TTSClient(config.elevenlabs_api_key, config.elevenlabs_voice_id, mock=tts_mock)
        self.gemini    = GeminiVision(api_key=config.gemini_api_key, mock=gemini_mock)
        self.cam_stream = CameraStreamServer(port=8766)
        self.camera    = CameraBridge(
            stream_url=PhoneCameraConfig.from_env(),
            fps_limit=2,        # Gemini has rate limits — 2fps is plenty
            mock=hw_mock,
        )
        self.dashboard = DashboardServer(port=config.ws_port)
        self.throttle  = AlertThrottle(cooldown_s=7.0)

        # Live state
        self._distance_cm:  Optional[float] = None
        self._scene:        GeminiScene     = GeminiScene()
        self._alert_queue:  list[dict]      = []
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True

        print()
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║  👁  Blind Pedestrian Navigation Agent                      ║")
        print("║      Gemini Vision → Priority Queue → ElevenLabs Voice      ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        print()

        self.bridge.connect()
        await self.bridge.start_reader()
        await self.dashboard.start()
        self.cam_stream.start()

        self.camera.add_frame_callback(self._on_frame)
        self.camera.start()

        self.tts.speak(
            "Pedestrian navigation agent online. "
            "Point the camera at the intersection ahead. I will guide you.",
            urgency="calm",
        )

        try:
            await asyncio.gather(
                self._distance_loop(),
                self._speak_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self.camera.stop()
            self.cam_stream.stop()
            self.bridge.disconnect()
            await self.dashboard.stop()
            logger.info("[Agent] Shutdown complete.")

    async def stop(self):
        self._running = False

    # ── Camera → Gemini (runs in camera thread) ───────────────────────────────

    def _on_frame(self, frame):
        """Called by CameraBridge on each frame. Runs in background thread."""
        try:
            # Forward raw frame to MJPEG stream server for dashboard preview
            if frame is not None:
                cam_update(frame)
            scene = self.gemini.analyze_frame(frame)
            self._scene = scene
            self._alert_queue = build_alert_queue(scene, self._distance_cm)
        except Exception as e:
            logger.error(f"[Gemini] Frame error: {e}")

    # ── Distance sensor loop ──────────────────────────────────────────────────

    async def _distance_loop(self):
        """Read HC-SR04 distance events from ESP32 serial."""
        async for event in self.bridge.events():
            if not self._running:
                break
            if event.event_type == "distance" and event.raw:
                cm = event.raw.get("cm", -1)
                if cm > 0:
                    self._distance_cm = float(cm)
                    # Immediately rebuild queue when distance changes significantly
                    self._alert_queue = build_alert_queue(self._scene, self._distance_cm)

    # ── Alert speaker loop ────────────────────────────────────────────────────

    async def _speak_loop(self):
        """
        Every 2s: look at the top of the priority queue and speak the
        highest-urgency alert that hasn't been recently spoken.
        """
        logger.info("[Agent] Alert speaker loop started")

        while self._running:
            await asyncio.sleep(2.0)
            queue = self._alert_queue

            if not queue:
                continue

            # Walk the queue in priority order — speak first unthrottled alert
            for alert in queue:
                key = self.throttle.key_for(alert)
                if self.throttle.should_speak(key):
                    self._dispatch(alert)
                    await self._push_dashboard(alert)
                    break

    # ── Output ────────────────────────────────────────────────────────────────

    def _dispatch(self, alert: dict):
        urgency = alert["urgency"]
        text    = alert["text"]
        haptic  = alert["haptic"]

        logger.info(f"[Alert] [{urgency.upper()}] {text}")
        self.bridge.send_haptic(haptic)
        self.bridge.send_display(str(text)[:24], urgency.upper())
        self.tts.speak(text, urgency=urgency)

    async def _push_dashboard(self, spoken_alert: dict):
        scene = self._scene
        state = {
            "level":         spoken_alert["urgency"].upper(),
            "speech":        spoken_alert["text"],
            "source":        spoken_alert.get("source", ""),
            "distance_cm":   float(f"{self._distance_cm:.1f}") if self._distance_cm is not None else None,
            "safe_to_cross": scene.safe_to_cross,
            "traffic_light": scene.traffic_light,
            "walk_signal":   scene.walk_signal,
            "scene_summary": scene.scene_summary,
            "alert_queue": [
                {
                    "priority": a["priority"],
                    "urgency":  a["urgency"],
                    "text":     a["text"],
                    "source":   a["source"],
                }
                for a in self._alert_queue
            ],
            "detections": [
                {
                    "label":         o.label,
                    "urgency":       o.urgency,
                    "position":      o.position,
                    "distance_hint": o.distance_hint,
                    "speech":        o.speech,
                }
                for o in scene.objects
            ],
            "timestamp": time.time(),
        }
        await self.dashboard.broadcast(state)
