"""
camera_server.py — Simple MJPEG HTTP stream server for the dashboard.

Serves the latest camera frame at http://localhost:8766/stream
so dashboard/index.html can display a live video feed via <img> tag.
Runs in a background thread alongside the main agent.
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

logger = logging.getLogger(__name__)


class MJPEGHandler(BaseHTTPRequestHandler):
    """Serves MJPEG frames to the dashboard."""

    def log_message(self, format, *args):
        pass  # suppress default request logging

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    frame_bytes = _latest_jpeg[0]
                    if frame_bytes:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(frame_bytes)
                        self.wfile.write(b"\r\n")
                    import time; time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/snapshot":
            frame_bytes = _latest_jpeg[0]
            if frame_bytes:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(frame_bytes)
            else:
                self.send_response(503)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


# Shared state — latest frame as JPEG bytes
_latest_jpeg: list[Optional[bytes]] = [None]


def update_frame(frame):
    """Call this with each new OpenCV BGR frame."""
    try:
        import cv2
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        _latest_jpeg[0] = bytes(buf)
    except Exception:
        pass


class CameraStreamServer:
    """Hosts the MJPEG stream on http://localhost:PORT/stream."""

    def __init__(self, port: int = 8766):
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._server = HTTPServer(("127.0.0.1", self.port), MJPEGHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="CamStream")
        self._thread.start()
        logger.info(f"[CamStream] MJPEG stream at http://localhost:{self.port}/stream")

    def stop(self):
        if self._server:
            self._server.shutdown()
