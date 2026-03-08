# 🚶 DigiGuide — Road Crossing Assistant

> An AI-powered accessibility agent for blind and deaf pedestrians — combining real-time computer vision, speech synthesis, haptic feedback, and an immersive Three.js interface.

Built at **SodaHacks 2026** · [Technical Design Flowchart](../flowchart.html) · [DESIGN.md](./DESIGN.md)

---

## What is DigiGuide?

DigiGuide is a wearable-style system built around the **ESP32 S3 Box 3** that helps disabled pedestrians safely cross roads. A live video feed is analyzed by **Gemini Vision API** to detect cars, cyclists, and pedestrians. For **blind users**, hazard context is spoken aloud through **ElevenLabs TTS**. For **deaf users**, a **piezoelectric haptic disk** vibrates in intensity proportional to the hazard distance, while the ESP32's built-in screen flashes a text warning. A companion **Three.js webapp** (glowing blob orb) visually reflects the system state in real time.

---

## Hardware

| Component | GPIO | Role |
|---|---|---|
| **ESP32-S3-BOX-3** | — | Central controller · display · WiFi bridge |
| **ESP32-CAM module** | Serial | Captures JPEG frames for Gemini |
| **Piezoelectric disk** | GPIO 4 | Haptic alerts via PWM — deaf users |
| **PIR motion sensor HC-SR501** | GPIO 5 | Proximity detection for precedence array |

### Wiring

```
Piezo disc (+)  →  GPIO 4  (via NPN transistor 2N2222 for sufficient drive)
Piezo disc (-)  →  GND
PIR OUT         →  GPIO 5
PIR VCC         →  3.3V
PIR GND         →  GND
ESP32-CAM TX    →  ESP32-S3-BOX-3 RX (GPIO 17)
ESP32-CAM RX    →  ESP32-S3-BOX-3 TX (GPIO 18)
```

---

## AI & API Stack

| Service | Purpose |
|---|---|
| **Gemini 2.0 Flash** | Vision — classifies objects in JPEG frames; returns JSON precedence array |
| **ElevenLabs Flash v2.5** | TTS — ultra-low-latency spoken hazard alerts (<75 ms) for blind users |
| **Hume AI EVI** | Detects pedestrian stress/fear from vocal prosody, escalates alerts |
| **Selenium (headless Chrome)** | Scrapes live traffic incidents (511.org, Google Maps Traffic) |

---

## Quick Start

### 1. Clone & create virtual environment

```bash
cd /Users/alex1602e19/Desktop/Guide/sodaHacks-main
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Fill in ELEVENLABS_API_KEY, HUME_API_KEY, GEMINI_API_KEY
```

### 3. Run in mock mode (no hardware required)

```bash
python main.py --mock --location "San Francisco, CA"
```

This will:
- Simulate ESP32 sensor events
- Cycle through Gemini mock hazard scenarios
- Log all pipeline activity to stdout + `agent.log`
- Start WebSocket server on `ws://localhost:8765`

### 4. Open the Three.js companion webapp

```bash
open webapp/index.html
```

> The orb glows **cyan** (safe), transitions to **amber** (caution), and pulses **red** (danger). When the backend is offline, a built-in demo cycle auto-plays.

### 5. Open the caregiver dashboard

```bash
open dashboard/index.html
```

### 6. Run with real ESP32 hardware

```bash
python main.py --port /dev/cu.usbserial-XXXX
```

---

## Project Structure

```
sodaHacks-main/
├── main.py                      # Entry point — async pipeline orchestration
├── requirements.txt             # Python dependencies
├── .env.example                 # API key template
│
├── agent/
│   ├── config.py                # Config loader (dotenv)
│   ├── agent_core.py            # SafetyAgent — 4 async loops + pipeline
│   ├── gemini_client.py         # NEW — Gemini Vision API frame analysis
│   ├── esp32_bridge.py          # Serial / WiFi bridge to ESP32
│   ├── tts_elevenlabs.py        # ElevenLabs TTS
│   ├── hume_client.py           # Hume emotion detection
│   ├── traffic_scraper.py       # Selenium traffic scraper
│   ├── hazard_classifier.py     # Sensor fusion → HazardLevel
│   ├── context_packet.py        # ContextPacket data spine
│   └── ws_server.py             # WebSocket → dashboard + webapp
│
├── webapp/
│   └── index.html               # NEW — Three.js glowing blob orb UI
│
├── dashboard/
│   └── index.html               # Caregiver monitoring dashboard
│
├── esp32_firmware/
│   └── blind_agent_firmware.ino # Arduino firmware for ESP32-S3-BOX-3
│
└── tests/
    ├── test_hazard_classifier.py
    ├── test_context_packet.py
    └── test_gemini_client.py    # NEW — Gemini client mock tests
```

---

## Haptic Patterns (Deaf Mode)

| Pattern | Feel | Meaning |
|---|---|------|
| `calm` | Soft 80 ms buzz | SAFE — system alive |
| `pulse` | Single 300 ms buzz | CAUTION — motion nearby |
| `double` | Two quick buzzes | Moderate hazard |
| `rapid` | Triple rapid bursts | DANGER — stop immediately |

---

## Pipeline (per cycle, target < 2 s)

```
ESP32-CAM JPEG → Python Backend → Gemini Vision API (~800 ms)
                                ↓ hazard JSON
                         Precedence Array (sorted by priority)
                  ┌──────────────────────────────────────────┐
                  │ BLIND      ElevenLabs TTS → Speaker      │
                  │ DEAF       Piezo PWM + ESP32 Screen       │
                  └──────────────────────────────────────────┘
                         WebSocket → Webapp orb + Dashboard
```

---

## Flashing the ESP32-S3-BOX-3

1. Install **Arduino IDE** → ESP32 board package (Espressif)
2. Install libraries: `ArduinoJson`, `LovyanGFX`
3. Open `esp32_firmware/blind_agent_firmware.ino`
4. Select board: **ESP32-S3-BOX** → Upload via USB-C

---

## Running Tests

```bash
pytest tests/ -v
```

Expected output:
```
tests/test_hazard_classifier.py  ......  PASSED
tests/test_context_packet.py     ....    PASSED
tests/test_gemini_client.py      .....   PASSED
```