"""
esp32_bridge.py — Serial communication layer between Python host and ESP32-S3-BOX-3.

The ESP32 firmware sends JSON events over USB serial, e.g.:
  {"event": "motion", "detected": true}
  {"event": "heartbeat", "uptime_ms": 12345}

Python sends JSON haptic commands, e.g.:
  {"haptic": "pulse"}
  {"haptic": "double"}
  {"haptic": "rapid"}
  {"haptic": "calm"}

Patterns map to distinct piezo vibration behaviours defined in the firmware.
"""

import json
import asyncio
import logging
from typing import AsyncGenerator, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Try importing pyserial; graceful fallback for testing without hardware ─────
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    logger.warning("pyserial not installed — running in mock serial mode.")


@dataclass
class SensorEvent:
    event_type: str       # "motion" | "heartbeat" | "error"
    detected: bool = False
    uptime_ms: int = 0
    raw: dict = None


HAPTIC_PATTERNS = {
    "calm":   "calm",     # single 100ms buzz — SAFE mode
    "pulse":  "pulse",    # one 300ms buzz — CAUTION, motion detected
    "double": "double",   # two quick buzzes — moderate hazard
    "rapid":  "rapid",    # continuous rapid buzz — DANGER
}


class SerialBridge:
    """
    Manages a USB serial connection to the ESP32-S3-BOX-3.
    Spawns a background reader task that emits SensorEvents.
    """

    def __init__(self, port: str, baud: int = 115200, mock: bool = False):
        self.port = port
        self.baud = baud
        self.mock = mock or not SERIAL_AVAILABLE
        self._serial: Optional["serial.Serial"] = None
        self._connected = False
        self._event_queue: asyncio.Queue[SensorEvent] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    # ── Connection lifecycle ───────────────────────────────────────────────────

    def connect(self) -> bool:
        if self.mock:
            logger.info("[MockSerial] ESP32 bridge running in simulation mode.")
            self._connected = True
            return True

        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=1,
                write_timeout=1,
            )
            self._connected = True
            logger.info(f"[Serial] Connected to ESP32 on {self.port} @ {self.baud} baud")
            return True
        except serial.SerialException as e:
            logger.error(f"[Serial] Failed to connect: {e}")
            self._connected = False
            return False

    def disconnect(self):
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("[Serial] Disconnected from ESP32")

    # ── Haptic commands ───────────────────────────────────────────────────────

    def send_haptic(self, pattern: str) -> bool:
        """Send a haptic vibration command to the ESP32 piezo driver."""
        if pattern not in HAPTIC_PATTERNS:
            logger.warning(f"[Haptic] Unknown pattern '{pattern}', using 'pulse'")
            pattern = "pulse"

        payload = json.dumps({"haptic": pattern}) + "\n"

        if self.mock:
            logger.info(f"[MockHaptic] 📳 Pattern: {pattern}")
            return True

        if not self._connected or not self._serial:
            logger.warning("[Haptic] Not connected — skipping haptic command")
            return False

        try:
            self._serial.write(payload.encode("utf-8"))
            self._serial.flush()
            logger.debug(f"[Haptic] Sent pattern: {pattern}")
            return True
        except serial.SerialException as e:
            logger.error(f"[Haptic] Write failed: {e}")
            return False

    def send_display(self, text: str, level: str = "SAFE"):
        """Send display update command to ESP32 LCD."""
        payload = json.dumps({"display": {"text": text, "level": level}}) + "\n"
        if self.mock:
            logger.info(f"[MockDisplay] 🖥  [{level}] {text}")
            return
        if self._serial:
            try:
                self._serial.write(payload.encode("utf-8"))
            except Exception:
                pass

    # ── Sensor event reading ──────────────────────────────────────────────────

    async def start_reader(self):
        """Start background task that reads serial stream and queues events."""
        if self.mock:
            self._reader_task = asyncio.create_task(self._mock_reader())
        else:
            self._reader_task = asyncio.create_task(self._serial_reader())

    async def events(self) -> AsyncGenerator[SensorEvent, None]:
        """Async generator — yields SensorEvents as they arrive."""
        while self._connected:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                continue

    # ── Internal readers ──────────────────────────────────────────────────────

    async def _serial_reader(self):
        """Read JSON lines from ESP32 serial port."""
        logger.info("[Serial] Reader started")
        loop = asyncio.get_event_loop()
        while self._connected:
            try:
                line = await loop.run_in_executor(None, self._readline)
                if line:
                    event = self._parse_event(line)
                    if event:
                        await self._event_queue.put(event)
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Serial] Reader error: {e}")
                await asyncio.sleep(0.5)

    def _readline(self) -> Optional[str]:
        if self._serial and self._serial.in_waiting:
            try:
                return self._serial.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                return None
        return None

    async def _mock_reader(self):
        """Simulates PIR sensor firing periodically for testing."""
        import random
        logger.info("[MockSerial] Simulated PIR reader started — motion fires every ~15s")
        while self._connected:
            await asyncio.sleep(random.uniform(10, 20))
            event = SensorEvent(event_type="motion", detected=True, raw={"event": "motion", "detected": True})
            await self._event_queue.put(event)
            logger.info("[MockSerial] 🚨 Simulated PIR motion event")

    @staticmethod
    def _parse_event(line: str) -> Optional[SensorEvent]:
        try:
            data = json.loads(line)
            event_type = data.get("event", "unknown")
            return SensorEvent(
                event_type=event_type,
                detected=data.get("detected", False),
                uptime_ms=data.get("uptime_ms", 0),
                raw=data,
            )
        except json.JSONDecodeError:
            logger.debug(f"[Serial] Non-JSON line: {line}")
            return None
