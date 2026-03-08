# 🚗 Blind Pedestrian Safety Agent

> An AI-powered agent for blind/visually-impaired pedestrians — combining tactile, audio, and real-time traffic awareness.

## Hardware

| Component | Role |
|---|---|
| **ESP32-S3-BOX-3** | Edge compute + display + serial bridge |
| **Piezoelectric discs** (GPIO 4) | Haptic alerts via PWM patterns |
| **PIR motion sensor HC-SR501** (GPIO 5) | Detects pedestrians, approaching objects |

## AI Integrations

| Service | Purpose |
|---|---|
| **ElevenLabs** Flash v2.5 | Ultra-low-latency TTS voice alerts (<75ms) |
| **Hume AI** | Driver stress/fear detection from vocal prosody |
| **Selenium** | Scrapes Google Maps Traffic + 511.org for incidents |

---

## Quick Start

### 1. Setup Python environment

```bash
cd /Users/Anisha/Downloads/sodaHacks
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and fill in ELEVENLABS_API_KEY and HUME_API_KEY
```

### 3. Run in mock mode (no hardware needed)

```bash
python main.py --mock --location "San Francisco, CA"
```

### 4. Run with real ESP32 hardware

```bash
python main.py --port /dev/cu.usbserial-XXXX
```

### 5. Open the caregiver dashboard

```
open dashboard/index.html
```

---

## Flashing the ESP32-S3-BOX-3

1. Open **Arduino IDE** → install **ESP32 board package** (Espressif)
2. Install libraries:
   - `ArduinoJson` (bblanchon)
   - `LovyanGFX` (lovyan03)
3. Open `esp32_firmware/blind_agent_firmware.ino`
4. Select board: **ESP32-S3-BOX**
5. Upload via USB-C

### Wiring

```
Piezo disc → PIN_PIEZO (GPIO 4)  + GND
PIR OUT    → PIN_PIR  (GPIO 5)
PIR VCC    → 3.3V
PIR GND    → GND
```

> **Note:** For stronger haptic feedback, drive the piezo through a NPN transistor (e.g. 2N2222) or MOSFET — GPIO alone may not supply enough current.

---

## Project Structure

```
sodaHacks/
├── main.py                    # Entry point
├── requirements.txt
├── .env.example               # API key template
├── agent/
│   ├── config.py              # Config loader
│   ├── esp32_bridge.py        # USB serial comms with ESP32
│   ├── tts_elevenlabs.py      # ElevenLabs TTS
│   ├── hume_client.py         # Hume emotion detection
│   ├── traffic_scraper.py     # Selenium traffic scraper
│   ├── hazard_classifier.py   # Sensor fusion → HazardLevel
│   ├── agent_core.py          # Main async orchestration loop
│   └── ws_server.py           # WebSocket → dashboard
├── esp32_firmware/
│   └── blind_agent_firmware.ino
├── dashboard/
│   └── index.html             # Live caregiver dashboard
└── tests/
    └── test_hazard_classifier.py
```

## Haptic Patterns

| Pattern | Feel | Meaning |
|---|---|---|
| `calm` | Soft 80ms buzz | SAFE — system alive |
| `pulse` | Single 300ms buzz | CAUTION — motion / mild alert |
| `double` | Two quick buzzes | Moderate hazard |
| `rapid` | Triple rapid bursts | DANGER — immediate hazard |

## Running Tests

```bash
pytest tests/ -v
```
