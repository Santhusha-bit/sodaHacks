"""
main.py — DigiGuide entry point.

Usage:
  python main.py                           # Uses .env config, blind mode
  python main.py --mode deaf               # Deaf mode (haptic + screen)
  python main.py --mock                    # Simulated mode (no hardware)
  python main.py --mock --mode deaf        # Full simulation, deaf pipeline
  python main.py --port /dev/cu.usbserial-X  # Custom serial port
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
        description="🚶 DigiGuide — Road-crossing assistant for disabled pedestrians",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mode blind --mock
  python main.py --mode deaf  --port /dev/cu.usbserial-0001
        """,
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["blind", "deaf"],
        help="User mode: 'blind' (audio via ElevenLabs) or 'deaf' (haptic + screen). Overrides .env USER_MODE.",
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

    # CLI overrides
    if args.port:
        os.environ["SERIAL_PORT"] = args.port
    if args.mode:
        os.environ["USER_MODE"] = args.mode

    # Load config
    try:
        from agent.config import load_config
        config = load_config()
    except EnvironmentError as e:
        print(f"\n❌  {e}\n")
        sys.exit(1)

    mode = config.user_mode   # "blind" or "deaf"
    mock_tag = "  [MOCK MODE — No hardware required]" if args.mock else ""

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║        🚶  DigiGuide Road-Crossing Assistant  🚶          ║")
    print("║   ElevenLabs · Gemini Vision · VAPI · ESP32-S3-BOX-3    ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  Mode        : {'👁  BLIND — Audio (ElevenLabs TTS)' if mode == 'blind' else '🤟 DEAF  — Haptic + Screen (ESP32)'}")
    print(f"  Serial port : {config.serial_port}")
    print(f"  Gemini model: {config.gemini_model}")
    print(f"  Dashboard   : http://localhost:{config.ws_port}  (open dashboard/index.html)")
    print(f"  Hume AI     : {'✅ Active' if config.hume_api_key else '⬜ Skipped (no key)'}")
    print(f"  VAPI        : {'✅ Active' if config.vapi_api_key else '⬜ Skipped (no key)'}")
    print(f"  Mock mode   :{mock_tag if mock_tag else ' OFF'}")
    print()

    from agent.agent_core import SafetyAgent
    agent = SafetyAgent(config=config, mock=args.mock, mode=mode)

    try:
        await agent.run()
    except KeyboardInterrupt:
        print("\n\n⏹  DigiGuide stopped by user.")
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
