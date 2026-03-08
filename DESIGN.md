# DigiGuide — Technical Design Document

**SodaHacks 2026 · Version 1.0 · 2026-03-07 · Status: DRAFT**

See also: [Interactive Flowchart](../flowchart.html)

---

## 1. Overview

DigiGuide is a real-time road-crossing assistant for disabled pedestrians, built on the **ESP32 S3 Box 3** edge device. It fuses computer vision (Gemini), traffic intelligence (Selenium), and emotion detection (Hume) into a sub-2-second alert loop that outputs audio (ElevenLabs TTS) for blind users and haptic+visual feedback for deaf users.

**Latency target:** Camera capture → user alert < **2.0 seconds**

**Two user modes:**
- **Blind** — spoken alerts via ElevenLabs TTS
- **Deaf** — piezoelectric haptic buzz + ESP32 S3 Box 3 screen warning

---

## 2. System Architecture

```
┌────────────────────────────────── HARDWARE ──────────────────────────────────┐
│  Real World ──→ ESP32-CAM (JPEG) ──→ ESP32 S3 Box 3 ──→ PIR Sensor         │
│                                              │                Piezoelectric   │
└──────────────────────────────────────────────┼────────────────── Disk ───────┘
                                               │ WiFi / Serial
┌────────────────────────────────── PROCESSING ─────────────────────────────────┐
│                                              ▼                                 │
│  Python Backend (Flask/asyncio)                                                │
│    ├─ gemini_client.py  ──→  Gemini Vision API  ──→  hazard JSON              │
│    ├─ traffic_scraper.py ──→ Selenium ──→ TrafficAlert[]                      │
│    ├─ hume_client.py ──→ Hume EVI ──→ EmotionScores                          │
│    ├─ hazard_classifier.py ──→ HazardLevel (SAFE / CAUTION / DANGER)         │
│    └─ ws_server.py ──→ WebSocket (port 8765)                                  │
└────────────────────────────────── OUTPUT ─────────────────────────────────────┘
                 │ BLIND                        │ DEAF
                 ▼                              ▼
         ElevenLabs TTS               Piezo PWM + ESP32 Screen
         Spoken alert                 Haptic buzz + "CAR – 8 FT LEFT"
                 │                              │
                 └──────────── WebSocket ───────┘
                                    │
                            Three.js Webapp
                         (Glowing Orb — cyan/amber/red)
```

---

## 3. Component Descriptions

| Component | Type | Role | Interface |
|---|---|---|---|
| ESP32-CAM | Hardware | 5 fps JPEG stream | Serial → ESP32 S3 Box 3 |
| ESP32 S3 Box 3 | Hardware | Central controller, display, WiFi | GPIO 4 (piezo), GPIO 5 (PIR) |
| PIR HC-SR501 | Hardware | Proximity detection | GPIO 5 digital read |
| Piezoelectric Disk | Hardware | Haptic vibration (deaf users) | PWM via GPIO 4 + NPN transistor |
| `gemini_client.py` | Software | Vision— JPEG → hazard JSON | REST (google-generativeai SDK) |
| `tts_elevenlabs.py` | Software | TTS— text → MP3 → speaker | REST (elevenlabs SDK) |
| `hume_client.py` | Software | Emotion detection from mic | WebSocket (Hume EVI) |
| `traffic_scraper.py` | Software | Scrape live traffic incidents | Selenium headless Chrome |
| `hazard_classifier.py` | Software | Fuse all signals → HazardLevel | Pure Python |
| `ws_server.py` | Software | Broadcast state to clients | WebSocket port 8765 |
| `webapp/index.html` | Frontend | Three.js orb UI | WebSocket consumer |
| `dashboard/index.html` | Frontend | Caregiver monitoring dashboard | WebSocket consumer |

---

## 4. Data Structures

### 4.1 Gemini Precedence Array

Returned by `GeminiVisionClient.analyze_frame()`, sorted ascending by `priority`:

```json
[
  { "label": "car",        "direction": "left",  "distance_ft": 8,  "priority": 1 },
  { "label": "cyclist",    "direction": "ahead", "distance_ft": 25, "priority": 2 },
  { "label": "pedestrian", "direction": "right", "distance_ft": 40, "priority": 3 }
]
```

Priority ranking rule: `moving vehicle (1) < stationary vehicle < person < other (N)`

### 4.2 ContextPacket (data spine)

```python
ContextPacket:
  sensor:          SensorData       # pir_triggered, uptime_ms
  user_voice:      UserVoice        # transcript, hume_emotions, has_question
  scraped_context: ScrapedContext   # traffic[], weather, nearby_pois
  response:        AgentResponse    # text, haptic, esp32_display, hazard_level
  gemini_hazards:  list[dict]       # precedence array from Gemini
```

### 4.3 WebSocket Broadcast Schema

JSON pushed to all connected clients every pipeline cycle:

```json
{
  "level":          "SAFE | CAUTION | DANGER",
  "speech":         "Car approaching from your left, approximately 8 feet.",
  "haptic":         "calm | pulse | double | rapid",
  "esp32_display":  "SAFE | CAUTION | DANGER",
  "pir":            false,
  "distance_ft":    8,
  "reasons":        ["PIR triggered", "Car 8ft left"],
  "traffic_alerts": [
    { "severity": "high", "description": "...", "location": "..." }
  ],
  "emotion": {
    "stress": 0.12,
    "calm":   0.85,
    "fear":   0.03,
    "summary": "calm (0.85)"
  }
}
```

---

## 5. Pipeline Flow (per 2-second cycle)

```
Phase 1 — INPUT      Build ContextPacket from current sensor state
Phase 2 — ENRICHING  Attach latest Gemini hazards + Selenium traffic
Phase 3 — CLASSIFY   HazardClassifier → HazardLevel + reasons[]
Phase 4 — RESPOND    Build AgentResponse (text, haptic, display command)
Phase 5 — OUTPUT     ElevenLabs TTS + ESP32 serial + WebSocket broadcast
```

**Latency budget:**

| Step | Target |
|---|---|
| ESP32-CAM capture | ~50 ms |
| WiFi / Serial transfer | ~80 ms |
| Gemini Vision API | ~800 ms |
| Hazard classification | ~5 ms |
| ElevenLabs TTS synthesis | ~350 ms |
| Haptic + display dispatch | ~30 ms |
| **Total** | **< 1.4 s** |

---

## 6. User Mode Pipelines

### 6.1 Blind User — Audio Pipeline

```
01  ESP32-CAM captures JPEG frame at 5 fps
02  Gemini Vision API → hazard JSON precedence array
03  Python: merge PIR distance + Gemini → rank top hazard
04  ElevenLabs TTS → spoken MP3 → ESP32 speaker
05  Three.js orb glows & pulses in sync with playback
```

### 6.2 Deaf User — Haptic + Screen Pipeline

```
01  ESP32-CAM + PIR sensor → dual capture (image + distance)
02  Gemini Vision API → object classes + spatial tags
03  Python: map top hazard distance → PWM intensity [0–255]
04a Piezo disk receives PWM → haptic buzz ∝ proximity
04b ESP32 S3 Box 3 screen: "CAR – 8 FT LEFT"
05  Three.js orb flashes red, proximity ring scales
```

---

## 7. Configuration Reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | Google AI Studio key |
| `ELEVENLABS_API_KEY` | — | ElevenLabs key |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | TTS voice (Rachel) |
| `HUME_API_KEY` | — | Hume AI key |
| `SERIAL_PORT` | `/dev/cu.usbserial-0001` | ESP32 serial port |
| `SERIAL_BAUD` | `115200` | Baud rate |
| `SCRAPE_LOCATION` | `San Francisco, CA` | Traffic scrape location |
| `TRAFFIC_POLL_INTERVAL` | `120` | Seconds between scrapes |
| `PIR_COOLDOWN_SECONDS` | `5` | Min time between PIR alerts |
| `HUME_STRESS_THRESHOLD` | `0.65` | Escalation trigger |
| `WS_PORT` | `8765` | WebSocket dashboard port |

---

## 8. Deployment Notes

### Development (mock mode — no hardware)
```bash
python main.py --mock --location "San Francisco, CA"
```

### Production (with ESP32 hardware)
```bash
python main.py --port /dev/cu.usbserial-XXXX
```

### Serving the webapp locally
```bash
# Simple HTTP server (avoids importmap file:// restrictions)
cd sodaHacks-main
python3 -m http.server 8080
# Then open: http://localhost:8080/webapp/
```

---

## 9. Extension Points

- **Add Gemini to agent_core**: integrate `GeminiVisionClient` into `_run_pipeline()`, calling `analyze_frame()` with each ESP32-CAM JPEG batch and merging results into `ContextPacket.gemini_hazards`.
- **Gemini API key**: add `GEMINI_API_KEY` to `.env.example` and `config.py`.
- **Camera stream**: extend `esp32_bridge.py` to yield JPEG frames from the camera serial port alongside sensor events.
- **On-device display**: extend `esp32_bridge.py`'s `send_display()` to include direction (`"CAR – 8 FT LEFT"`) built from the top Gemini hazard.
