"""
main.py — Entry point for the Blind Pedestrian Navigation Agent.

Usage:
  python main.py                                    # real hardware + real camera
  python main.py --mock                             # full simulation
  python main.py --camera http://192.168.1.5:8080/video
  python main.py --port /dev/cu.usbserial-XXXX --mock
"""

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="👁  Blind Pedestrian Navigation Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Phone camera setup:
  Android: Install 'IP Webcam' app → Start server → use http://PHONE_IP:8080/video
  iOS:     Install 'EpocCam' or use USB then pass --camera 1

Examples:
  python main.py --mock
  python main.py --camera http://192.168.1.42:8080/video --port /dev/cu.usbserial-0001
        """,
    )
    p.add_argument("--mock",     action="store_true",
                   help="Simulation mode (no hardware, no real camera)")
    p.add_argument("--port",     type=str, default=None,
                   help="ESP32 serial port (overrides .env SERIAL_PORT)")
    p.add_argument("--camera",   type=str, default=None,
                   help="Phone camera URL (overrides .env CAMERA_STREAM_URL)")
    p.add_argument("--log-level", type=str, default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def run_web_server():
    """Simple static file server for the dashboard."""
    import http.server
    import socketserver
    import os

    PORT = 8080
    DIRECTORY = os.path.join(os.path.dirname(__file__), "dashboard")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=DIRECTORY, **kwargs)

        def log_message(self, format, *args):
            pass  # Suppress logs to keep terminal clean

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        logger.info(f"🌐 Web dashboard available at http://localhost:{PORT}")
        httpd.serve_forever()


async def main():
    args = parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Start web server in background thread
    import threading
    threading.Thread(target=run_web_server, daemon=True).start()

    if args.port:
        os.environ["SERIAL_PORT"] = args.port
    if args.camera:
        os.environ["CAMERA_STREAM_URL"] = args.camera

    try:
        from agent.config import load_config
        config = load_config()
    except EnvironmentError as e:
        print(f"\n❌  {e}\n")
        sys.exit(1)

    from agent.pedestrian_agent import PedestrianAgent
    agent = PedestrianAgent(config=config, mock=args.mock)

    try:
        await agent.run()
    except KeyboardInterrupt:
        print("\n⏹  Agent stopped.")
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
