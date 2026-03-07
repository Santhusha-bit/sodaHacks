"""
tests/test_hazard_classifier.py — Unit tests for the hazard classification logic.
"""
import pytest
from agent.hazard_classifier import HazardClassifier, HazardLevel
from agent.hume_client import EmotionReading
from agent.traffic_scraper import TrafficAlert


@pytest.fixture
def clf():
    return HazardClassifier(stress_threshold=0.65)


def calm_emotion():
    return EmotionReading(stress=0.1, calm=0.9, fear=0.05)


def stressed_emotion():
    return EmotionReading(stress=0.8, calm=0.2, fear=0.4)


# ── SAFE scenarios ────────────────────────────────────────────────────────────
def test_all_clear_is_safe(clf):
    a = clf.classify(pir_triggered=False, traffic_alerts=[], emotion=calm_emotion())
    assert a.level == HazardLevel.SAFE
    assert a.speech_message is None


# ── CAUTION scenarios ─────────────────────────────────────────────────────────
def test_pir_only_is_caution(clf):
    a = clf.classify(pir_triggered=True, traffic_alerts=[], emotion=calm_emotion())
    assert a.level == HazardLevel.CAUTION
    assert a.haptic_pattern == "pulse"
    assert "motion" in a.reasons[0].lower()


def test_medium_traffic_is_caution(clf):
    alerts = [TrafficAlert("medium", "Construction on I-80", "Highway 101", "test")]
    a = clf.classify(pir_triggered=False, traffic_alerts=alerts, emotion=calm_emotion())
    assert a.level == HazardLevel.CAUTION


def test_elevated_stress_is_caution(clf):
    emotion = EmotionReading(stress=0.6, calm=0.4, fear=0.1)
    a = clf.classify(pir_triggered=False, traffic_alerts=[], emotion=emotion)
    assert a.level == HazardLevel.CAUTION


# ── DANGER scenarios ──────────────────────────────────────────────────────────
def test_high_traffic_is_danger(clf):
    alerts = [TrafficAlert("high", "Major accident blocking all lanes", "Market St", "test")]
    a = clf.classify(pir_triggered=False, traffic_alerts=alerts, emotion=calm_emotion())
    assert a.level == HazardLevel.DANGER
    assert a.haptic_pattern == "rapid"


def test_pir_plus_traffic_escalates_to_danger(clf):
    alerts = [TrafficAlert("medium", "Slow traffic", "Hwy 1", "test")]
    a = clf.classify(pir_triggered=True, traffic_alerts=alerts, emotion=calm_emotion())
    assert a.level == HazardLevel.DANGER


def test_extreme_stress_is_danger(clf):
    a = clf.classify(pir_triggered=False, traffic_alerts=[], emotion=stressed_emotion())
    assert a.level == HazardLevel.DANGER


# ── Response fields ───────────────────────────────────────────────────────────
def test_danger_has_speech(clf):
    alerts = [TrafficAlert("high", "Crash on bridge", "Bay Bridge", "test")]
    a = clf.classify(pir_triggered=False, traffic_alerts=alerts, emotion=calm_emotion())
    assert a.speech_message is not None
    assert len(a.speech_message) > 5


def test_display_text_matches_level(clf):
    a = clf.classify(pir_triggered=True, traffic_alerts=[], emotion=calm_emotion())
    assert a.display_text == "CAUTION"
