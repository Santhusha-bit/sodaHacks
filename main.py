"""
main.py — Entry point for the Blind Driver Safety Agent.

Usage:
  python main.py                              # Uses .env config, real hardware
  python main.py --mock                       # Simulated mode (no hardware)
  python main.py --port /dev/cu.usbserial-X  # Custom serial port
  python main.py --location "Austin, TX"     # Custom traffic location
  python main.py --mock --location "NYC"     # Full simulation
"""

import argparse
import asyncio
import logging
import os
import sys

# ── Configure logging ─────────────────────────────────────────────────────────
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
    parser = argparse.ArgumentParser(
        description="🚗 Blind Driver Safety Agent — ElevenLabs + Hume + ESP32",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mock
  python main.py --port /dev/cu.usbserial-0001 --location "San Francisco, CA"
        """,
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in simulation mode (no hardware, mocked APIs)",
    )
    parser.add_argument(
        "--port",
        type=str,
        default=None,
        help="Serial port for ESP32 (overrides .env SERIAL_PORT)",
    )
    parser.add_argument(
        "--location",
        type=str,
        default=None,
        help="Traffic location to monitor (overrides .env SCRAPE_LOCATION)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    # Apply log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Override env vars from CLI args
    if args.port:
        os.environ["SERIAL_PORT"] = args.port
    if args.location:
        os.environ["SCRAPE_LOCATION"] = args.location

    # Load config
    try:
        from agent.config import load_config
        config = load_config()
    except EnvironmentError as e:
        print(f"\n❌  {e}\n")
        sys.exit(1)

    # Print startup banner
    mock_tag = "  [MOCK MODE — No hardware required]" if args.mock else ""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║      🚗  Blind Driver Safety Agent  🚗               ║")
    print("║          ElevenLabs + Hume AI + ESP32-S3-BOX-3       ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"  Serial port : {config.serial_port}")
    print(f"  Location    : {config.scrape_location}")
    print(f"  Dashboard   : http://localhost:{config.ws_port} (open dashboard/index.html)")
    print(f"  Mock mode   :{mock_tag if mock_tag else ' OFF'}")
    print()

    from agent.agent_core import SafetyAgent
    agent = SafetyAgent(config=config, mock=args.mock)

    try:
        await agent.run()
    except KeyboardInterrupt:
        print("\n\n⏹  Agent stopped by user.")
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
