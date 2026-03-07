"""
camera_bridge.py — Receives video frames from a phone camera over WiFi/HTTP.

The phone runs a simple MJPEG streaming app (e.g. "IP Webcam" on Android,
"EpocCam" or "Camo" on iOS) and this bridge polls the stream, decodes frames,
and hands them to the VisionDetector.

URL format: http://PHONE_IP:8080/video  (MJPEG stream)

Alternatively supports:
  - Direct USB webcam (index 0) for testing
  - Mock mode that generates synthetic "frames" without any camera
"""

import asyncio
import logging
import threading
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("[Camera] opencv-python not installed — "
                   "install with: pip install opencv-python")

# Frame type: numpy ndarray H x W x 3 BGR uint8 (or None in mock mode)
from typing import Any
Frame = Any


class CameraBridge:
    """
    Continuously reads frames from a phone MJPEG stream (or USB webcam).
    Calls `on_frame(frame)` callback for each new frame.
    Runs in a background thread so it doesn't block the async agent loop.
    """

    def __init__(
        self,
        stream_url: str = "http://192.168.1.100:8080/video",
        fps_limit: int = 5,           # process max N frames/second
        mock: bool = False,
    ):
        self.stream_url = stream_url
        self.frame_interval = 1.0 / fps_limit
        self.mock = mock or not CV2_AVAILABLE

        self._latest_frame: Optional[Frame] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._on_frame_callbacks: list[Callable] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def add_frame_callback(self, cb: Callable):
        """Register a function to call with each new frame."""
        self._on_frame_callbacks.append(cb)

    def start(self):
        """Start the background camera reader thread."""
        self._running = True
        target = self._mock_reader if self.mock else self._camera_reader
        self._thread = threading.Thread(target=target, daemon=True, name="CameraThread")
        self._thread.start()
        logger.info(f"[Camera] {'Mock' if self.mock else 'Live'} camera bridge started")

    def stop(self):
        """Stop reading frames."""
        self._running = False
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def get_latest_frame(self) -> Optional[Frame]:
        """Return the most recently captured frame (thread-safe)."""
        with self._lock:
            return self._latest_frame

    # ── Internal readers ──────────────────────────────────────────────────────

    def _camera_reader(self):
        """Read MJPEG stream from phone WiFi URL or USB webcam index."""
        logger.info(f"[Camera] Connecting to: {self.stream_url}")

        # Support both URL streams and integer webcam index
        source = int(self.stream_url) if self.stream_url.isdigit() else self.stream_url
        cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            logger.error(f"[Camera] Cannot open stream: {self.stream_url}")
            logger.info("[Camera] Falling back to mock mode")
            self._mock_reader()
            return

        logger.info("[Camera] ✅ Stream connected")
        last_process = 0.0

        while self._running:
            ret, frame = cap.read()
            if not ret:
                logger.warning("[Camera] Frame read failed — retrying")
                time.sleep(0.5)
                continue

            now = time.time()
            if now - last_process >= self.frame_interval:
                last_process = now
                with self._lock:
                    self._latest_frame = frame

                # Notify all registered callbacks
                for cb in self._on_frame_callbacks:
                    try:
                        cb(frame)
                    except Exception as e:
                        logger.error(f"[Camera] Callback error: {e}")

        cap.release()
        logger.info("[Camera] Stream closed")

    def _mock_reader(self):
        """Generate synthetic frames at the configured FPS for testing."""
        logger.info("[Camera] 🎭 Mock camera generating synthetic frames every "
                    f"{self.frame_interval:.1f}s")

        frame_count = 0
        while self._running:
            time.sleep(self.frame_interval)

            # Create a simple synthetic frame (blank with frame number overlay)
            # In mock mode the VisionDetector.analyze_frame() returns mock scenes
            synthetic = self._make_synthetic_frame(frame_count)
            frame_count += 1

            with self._lock:
                self._latest_frame = synthetic

            for cb in self._on_frame_callbacks:
                try:
                    cb(synthetic)
                except Exception as e:
                    logger.error(f"[Camera] Mock callback error: {e}")

    @staticmethod
    def _make_synthetic_frame(frame_n: int):
        """Make a blank 640x480 BGR frame (used only in mock mode)."""
        if not CV2_AVAILABLE:
            return None
        import numpy as np
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, f"MOCK FRAME {frame_n}", (20, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        return frame


class PhoneCameraConfig:
    """
    Helper to construct the camera stream URL.
    
    Popular phone camera apps and their stream URLs:
      IP Webcam (Android):  http://PHONE_IP:8080/video
      DroidCam (Android):   http://PHONE_IP:4747/mjpegfeed
      EpocCam (iOS):        Use USB mode then pass index 1
    
    Set CAMERA_STREAM_URL in your .env file.
    """
    @staticmethod
    def from_env() -> str:
        import os
        url = os.getenv("CAMERA_STREAM_URL", "0")
        logger.info(f"[Camera] Using stream: {url}")
        return url
