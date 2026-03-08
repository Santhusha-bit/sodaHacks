"""
pedestrian_agent.py — Blind Pedestrian Navigation Agent (Gemini + ElevenLabs pipeline).
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


# ── Alert throttle ────────────────────────────────────────────────────────────

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
        return f"{alert['urgency']}:{alert['text'][:40]}"


# ── Main Agent ────────────────────────────────────────────────────────────────

class PedestrianAgent:
    def __init__(self, config: Config, mock: bool = False):
        self.config = config
        self._running = False
        self._scene = GeminiScene()
        self._distance_cm = 400.0 # Default to safe distance (4 meters)
        self._alert_queue = []
        self._loop = None # Main event loop for thread-safe broadcasts

        # Subsystems
        hw_mock = mock
        tts_mock = not bool(config.elevenlabs_api_key)
        gemini_mock = not bool(config.gemini_api_key)

        self.bridge = SerialBridge(config.serial_port, config.serial_baud, mock=hw_mock)
        self.tts = TTSClient(config.elevenlabs_api_key, config.elevenlabs_voice_id, mock=tts_mock)
        self.gemini = GeminiVision(api_key=config.gemini_api_key, mock=gemini_mock)
        self.cam_stream = CameraStreamServer(port=8766)
        self.camera = CameraBridge(
            stream_url=PhoneCameraConfig.from_env(),
            fps_limit=0.20, # ~1 frame every 5s (12 RPM vs 20 RPM limit)
            mock=False,
        )
        self.dashboard = DashboardServer(port=config.ws_port)
        self.throttle = AlertThrottle(cooldown_s=7.0)

    async def run(self):
        self._running = True
        self._loop = asyncio.get_running_loop()

        print("\n╔══════════════════════════════════════════════════════════════╗")
        print("║  👁  Blind Pedestrian Navigation Agent                      ║")
        print("║      Gemini Vision → Priority Queue → ElevenLabs Voice      ║")
        print("╚══════════════════════════════════════════════════════════════╝\n")

        if not self.bridge.connect():
            if not self.config.serial_port or "usbserial" in self.config.serial_port:
                logger.info("[Agent] ESP32 hardware not detected. Enabling sensor simulation...")
                self.bridge.mock = True
                self.bridge.connect()
            else:
                logger.error("[Agent] Could not connect to ESP32. Check your SERIAL_PORT in .env.")
                # We'll still try to start the reader (it just won't do much if not connected)

        await self.bridge.start_reader()
        await self.dashboard.start()
        self.cam_stream.start()

        self.camera.add_frame_callback(self._on_frame)
        self.camera.start()

        self.tts.speak("Pedestrian navigation agent online.", urgency="calm")

        try:
            await asyncio.gather(self._distance_loop(), self._speak_loop())
        finally:
            await self.stop()

    async def stop(self):
        self._running = False
        self.camera.stop()
        self.cam_stream.stop()
        self.bridge.disconnect()
        await self.dashboard.stop()
        logger.info("[Agent] Shutdown complete.")

    def _on_frame(self, frame):
        """Runs in CameraThread (background)."""
        if frame is None: return
        try:
            cam_update(frame)
            scene = self.gemini.analyze_frame(frame)
            self._scene = scene
            self._alert_queue = build_alert_queue(scene, self._distance_cm)

            # Thread-safe broadcast to the main loop (dashboard)
            if self._loop and self._loop.is_running():
                from dataclasses import asdict
                status = {
                    "urgency": scene.dominant_urgency,
                    "scene_summary": scene.scene_summary,
                    "detections": [asdict(obj) for obj in scene.objects],
                    "safe_to_cross": scene.safe_to_cross,
                    "traffic_light": scene.traffic_light,
                    "walk_signal": scene.walk_signal,
                    "distance_cm": self._distance_cm,
                }
                asyncio.run_coroutine_threadsafe(self.dashboard.broadcast(status), self._loop)
        except Exception as e:
            logger.error(f"[Gemini] Frame error: {e}")

    async def _distance_loop(self):
        async for event in self.bridge.events():
            if not self._running: break
            if event.event_type == "distance" and event.raw:
                cm = event.raw.get("cm", -1)
                if cm > 0:
                    self._distance_cm = float(cm)
                    self._alert_queue = build_alert_queue(self._scene, self._distance_cm)

    async def _speak_loop(self):
        while self._running:
            await asyncio.sleep(3.0)
            queue = self._alert_queue
            if not queue: continue
            for alert in queue:
                key = self.throttle.key_for(alert)
                if self.throttle.should_speak(key):
                    self._dispatch(alert)
                    await self._push_dashboard(alert)
                    break

    def _dispatch(self, alert: dict):
        urgency = alert["urgency"]
        text = alert["text"]
        logger.info(f"[Alert] [{urgency.upper()}] {text}")
        self.bridge.send_haptic(alert["haptic"])
        self.bridge.send_display(text[:24], urgency.upper())
        self.tts.speak(text, urgency=urgency)

    async def _push_dashboard(self, spoken_alert: dict):
        # Full state push for the dashboard log/Crossing ring
        state = {
            "level": spoken_alert["urgency"].upper(),
            "speech": spoken_alert["text"],
            "distance_cm": self._distance_cm,
            "scene_summary": self._scene.scene_summary,
            "safe_to_cross": self._scene.safe_to_cross,
            "traffic_light": self._scene.traffic_light,
            "walk_signal": self._scene.walk_signal,
            # (Dashboard also gets individual detections from _on_frame broadcast)
        }
        await self.dashboard.broadcast(state)
