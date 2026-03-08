"""
hazard_classifier.py — Fuses PIR sensor, traffic data, and Hume emotion
into a unified HazardLevel and selects appropriate haptic + speech responses.

HazardLevel:
  SAFE    → green, calm buzz, reassuring speech (or silence)
  CAUTION → yellow, single pulse, warning speech
  DANGER  → red, rapid buzz, urgent speech alert
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent.hume_client import EmotionReading
from agent.traffic_scraper import TrafficAlert

logger = logging.getLogger(__name__)


class HazardLevel(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DANGER = "DANGER"


# Maps HazardLevel → (haptic pattern, TTS urgency, display text)
HAZARD_RESPONSES = {
    HazardLevel.SAFE: {
        "haptic": "calm",
        "urgency": "calm",
        "display": "SAFE",
    },
    HazardLevel.CAUTION: {
        "haptic": "pulse",
        "urgency": "caution",
        "display": "CAUTION",
    },
    HazardLevel.DANGER: {
        "haptic": "rapid",
        "urgency": "danger",
        "display": "DANGER",
    },
}

# Stress/fear thresholds that escalate hazard level
STRESS_ELEV_THRESHOLD = 0.55   # Escalates SAFE→CAUTION
STRESS_DANGER_THRESHOLD = 0.75  # Escalates CAUTION→DANGER


@dataclass
class HazardAssessment:
    level: HazardLevel
    haptic_pattern: str
    tts_urgency: str
    speech_message: Optional[str]
    display_text: str
    reasons: list[str]


class HazardClassifier:
    """
    Combines sensor, traffic, and emotion inputs into a single HazardAssessment.
    Rule-based fusion — deterministic and real-time.
    """

    def __init__(self, stress_threshold: float = 0.65):
        self.stress_threshold = stress_threshold

    def classify(
        self,
        pir_triggered: bool,
        traffic_alerts: list[TrafficAlert],
        emotion: EmotionReading,
    ) -> HazardAssessment:

        reasons = []
        level = HazardLevel.SAFE

        # ── 1. Traffic data (highest priority if severe) ──────────────────────
        high_traffic = [a for a in traffic_alerts if a.is_high_severity]
        medium_traffic = [a for a in traffic_alerts if a.severity == "medium"]

        if high_traffic:
            level = HazardLevel.DANGER
            reasons.append(f"Traffic: {high_traffic[0].description[:60]}")
        elif medium_traffic:
            # Escalate to CAUTION only if not already at a higher level
            _ORDER = [HazardLevel.SAFE, HazardLevel.CAUTION, HazardLevel.DANGER]
            if _ORDER.index(level) < _ORDER.index(HazardLevel.CAUTION):
                level = HazardLevel.CAUTION
            reasons.append(f"Traffic advisory near {medium_traffic[0].location}")

        # ── 2. PIR motion detection ───────────────────────────────────────────
        if pir_triggered:
            # Escalate: SAFE→CAUTION, CAUTION→DANGER
            if level == HazardLevel.SAFE:
                level = HazardLevel.CAUTION
            elif level == HazardLevel.CAUTION:
                level = HazardLevel.DANGER
            reasons.append("Motion detected ahead")

        # ── 3. Driver emotion (Hume) ──────────────────────────────────────────
        emotion_summary = emotion.summary  # resolve @property once
        if emotion.stress > STRESS_DANGER_THRESHOLD or emotion.fear > STRESS_DANGER_THRESHOLD:
            level = HazardLevel.DANGER
            reasons.append(f"Driver distress detected ({emotion_summary})")
        elif emotion.stress > STRESS_ELEV_THRESHOLD or emotion.fear > STRESS_ELEV_THRESHOLD:
            if level == HazardLevel.SAFE:
                level = HazardLevel.CAUTION
            reasons.append(f"Driver stress elevated ({emotion_summary})")

        # ── 4. Build speech message ───────────────────────────────────────────
        speech = self._build_speech(level, pir_triggered, traffic_alerts, emotion)

        response = HAZARD_RESPONSES[level]
        assessment = HazardAssessment(
            level=level,
            haptic_pattern=response["haptic"],
            tts_urgency=response["urgency"],
            speech_message=speech,
            display_text=response["display"],
            reasons=reasons,
        )

        logger.info(f"[Classifier] {level.value} — reasons: {reasons or ['all clear']}")
        return assessment

    def _build_speech(
        self,
        level: HazardLevel,
        pir_triggered: bool,
        traffic_alerts: list[TrafficAlert],
        emotion: EmotionReading,
    ) -> Optional[str]:
        """Compose a natural language speech message based on hazard sources."""

        parts = []

        if level == HazardLevel.SAFE:
            # Only speak occasionally for SAFE (don't flood the user)
            return None

        if level == HazardLevel.DANGER:
            if pir_triggered and not traffic_alerts:
                parts.append("Caution! Movement detected in your path.")
            if traffic_alerts and traffic_alerts[0].is_high_severity:
                parts.append(f"Warning: {traffic_alerts[0].description}.")
            if emotion.fear > STRESS_DANGER_THRESHOLD:
                parts.append("You seem distressed. Please remain calm and stay focused.")

        elif level == HazardLevel.CAUTION:
            if pir_triggered:
                parts.append("Heads up, motion detected nearby.")
            if traffic_alerts:
                parts.append(f"Traffic advisory: {traffic_alerts[0].location}.")
            if emotion.stress > STRESS_ELEV_THRESHOLD:
                parts.append("Take a breath. Road conditions need your attention.")

        return " ".join(parts) if parts else f"Alert: {level.value.lower()} conditions ahead."
