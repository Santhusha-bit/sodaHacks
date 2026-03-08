/*
 * blind_agent_firmware.ino
 * ESP32-S3-BOX-3 Firmware — Blind Driver Safety Agent
 *
 * Hardware:
 *   GPIO 4  → Piezoelectric haptic disc (+ transistor/MOSFET driver recommended)
 *   GPIO 5  → PIR sensor OUT pin (HC-SR501)
 *   Built-in display driven via ESP-IDF / LovyanGFX
 *   USB-C serial → Python host at 115200 baud
 *
 * Protocol (JSON over serial):
 *   From ESP32 → Python:  {"event":"motion","detected":true}
 *   From Python → ESP32:  {"haptic":"rapid"}  or  {"display":{"text":"DANGER","level":"DANGER"}}
 *
 * Arduino Libraries needed (via Library Manager):
 *   - ArduinoJson  (bblanchon)
 *   - LovyanGFX    (lovyan03) — for BOX-3 built-in display
 */

#include <Arduino.h>
#include <ArduinoJson.h>

// ── Pin Definitions ───────────────────────────────────────────────────────────
#define PIN_PIEZO   4    // Piezoelectric haptic disc (PWM capable)
#define PIN_PIR     5    // HC-SR501 PIR sensor output

// ── Haptic Patterns ───────────────────────────────────────────────────────────
// Each pattern is a sequence of {on_ms, off_ms} pairs, terminated by {0,0}
struct HapticStep { uint16_t on_ms; uint16_t off_ms; };

const HapticStep PATTERN_CALM[]   = {{80,0},{0,0}};                           // single soft buzz
const HapticStep PATTERN_PULSE[]  = {{300,0},{0,0}};                          // one firm buzz
const HapticStep PATTERN_DOUBLE[] = {{150,80},{150,0},{0,0}};                 // two quick buzzes
const HapticStep PATTERN_RAPID[]  = {{80,60},{80,60},{80,60},{80,0},{0,0}};   // triple rapid burst

// ── State ─────────────────────────────────────────────────────────────────────
volatile bool pirTriggered = false;
unsigned long lastPirTime  = 0;
const unsigned long PIR_DEBOUNCE_MS = 2000;

// ── ISR: PIR Interrupt ────────────────────────────────────────────────────────
void IRAM_ATTR onPirRise() {
  unsigned long now = millis();
  if (now - lastPirTime > PIR_DEBOUNCE_MS) {
    pirTriggered = true;
    lastPirTime  = now;
  }
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  // PIR sensor
  pinMode(PIN_PIR, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_PIR), onPirRise, RISING);

  // Piezo: set to LOW (off) initially
  pinMode(PIN_PIEZO, OUTPUT);
  digitalWrite(PIN_PIEZO, LOW);

  // Startup haptic indication: one firm buzz = agent online
  playHaptic(PATTERN_PULSE);

  Serial.println("{\"event\":\"boot\",\"msg\":\"ESP32-S3-BOX-3 Safety Agent Online\"}");
}

// ── Main Loop ─────────────────────────────────────────────────────────────────
void loop() {
  // 1. Send PIR event if triggered
  if (pirTriggered) {
    pirTriggered = false;
    StaticJsonDocument<64> doc;
    doc["event"]    = "motion";
    doc["detected"] = true;
    doc["uptime_ms"] = millis();
    serializeJson(doc, Serial);
    Serial.println();
  }

  // 2. Heartbeat every 30s so Python knows we're alive
  static unsigned long lastHeartbeat = 0;
  if (millis() - lastHeartbeat > 30000) {
    StaticJsonDocument<64> doc;
    doc["event"]    = "heartbeat";
    doc["uptime_ms"] = millis();
    serializeJson(doc, Serial);
    Serial.println();
    lastHeartbeat = millis();
  }

  // 3. Read commands from Python host
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      handleCommand(line);
    }
  }

  delay(10);
}

// ── Command Handler ───────────────────────────────────────────────────────────
void handleCommand(const String& jsonStr) {
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, jsonStr);
  if (err) {
    Serial.print("{\"event\":\"error\",\"msg\":\"json_parse_failed\"}");
    Serial.println();
    return;
  }

  // Haptic command
  if (doc.containsKey("haptic")) {
    const char* pattern = doc["haptic"];
    executeHapticPattern(pattern);
  }

  // Display command (basic serial echo; extend with LovyanGFX for full display)
  if (doc.containsKey("display")) {
    const char* text  = doc["display"]["text"]  | "...";
    const char* level = doc["display"]["level"] | "SAFE";
    // TODO: Render text & level to BOX-3 LCD via LovyanGFX
    // For now, confirm via serial
    Serial.print("{\"event\":\"display_update\",\"text\":\"");
    Serial.print(text);
    Serial.print("\",\"level\":\"");
    Serial.print(level);
    Serial.println("\"}");
  }
}

// ── Haptic Dispatch ───────────────────────────────────────────────────────────
void executeHapticPattern(const char* name) {
  if (strcmp(name, "calm")   == 0) { playHaptic(PATTERN_CALM);   return; }
  if (strcmp(name, "pulse")  == 0) { playHaptic(PATTERN_PULSE);  return; }
  if (strcmp(name, "double") == 0) { playHaptic(PATTERN_DOUBLE); return; }
  if (strcmp(name, "rapid")  == 0) { playHaptic(PATTERN_RAPID);  return; }
  // Unknown pattern → default pulse
  playHaptic(PATTERN_PULSE);
}

// ── Haptic Player ─────────────────────────────────────────────────────────────
void playHaptic(const HapticStep* steps) {
  /*
   * Drive piezo via PWM (tone). The ESP32 analogWrite emulates tone output.
   * A 200Hz square wave provides a low, palpable rumble feel.
   * Adjust frequency for different sensation (100Hz = deep, 500Hz = sharp).
   */
  const uint16_t FREQ_HZ = 200;

  for (int i = 0; steps[i].on_ms != 0 || steps[i].off_ms != 0; i++) {
    if (steps[i].on_ms > 0) {
      tone(PIN_PIEZO, FREQ_HZ, steps[i].on_ms);
      delay(steps[i].on_ms);
      noTone(PIN_PIEZO);
    }
    if (steps[i].off_ms > 0) {
      delay(steps[i].off_ms);
    }
  }
}
