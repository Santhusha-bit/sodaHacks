"""
tests/test_context_packet.py — Tests for the ContextPacket JSON data model.
"""
import json
import pytest
from agent.context_packet import (
    ContextPacket, SensorData, EmotionScores, UserVoice,
    ScrapedContext, TrafficItem, AgentResponse,
)


# ── Construction helpers ──────────────────────────────────────────────────────

def make_packet_with_danger_traffic():
    p = ContextPacket()
    p.sensor = SensorData(pir_triggered=True, uptime_ms=5000)
    p.user_voice = UserVoice(
        transcript="What's happening around me?",
        hume_emotions=EmotionScores(stress=0.3, calm=0.7, fear=0.05),
        has_question=True,
    )
    p.scraped_context = ScrapedContext(
        traffic=[TrafficItem("high", "Crash blocking all lanes", "Market St", 0.2)],
        weather="Rainy, 52°F",
        nearby_pois=["Bus stop 0.05mi", "Hospital 0.3mi"],
    )
    p.response = AgentResponse(
        text="Warning. Crash on Market St.",
        urgency="danger",
        haptic="rapid",
        esp32_display="DANGER",
        hazard_level="DANGER",
    )
    p.phase = "complete"
    return p


# ── JSON serialisation ────────────────────────────────────────────────────────

def test_to_json_is_valid():
    p = make_packet_with_danger_traffic()
    raw = p.to_json()
    data = json.loads(raw)
    assert data["phase"] == "complete"
    assert data["response"]["hazard_level"] == "DANGER"
    assert data["sensor"]["pir_triggered"] is True


def test_to_serial_payload_is_compact():
    p = make_packet_with_danger_traffic()
    serial = p.to_serial_payload()
    assert serial.endswith("\n")
    parsed = json.loads(serial.strip())
    assert parsed["haptic"] == "rapid"
    assert parsed["level"] == "DANGER"
    # Serial payload should NOT include full scraped_context (keep it small)
    assert "traffic" not in parsed


def test_to_dashboard_state_has_all_fields():
    p = make_packet_with_danger_traffic()
    state = p.to_dashboard_state()
    for key in ["level", "haptic", "speech", "pir", "emotion", "traffic_alerts", "weather", "pois"]:
        assert key in state, f"Missing key: {key}"
    assert state["pir"] is True
    assert state["weather"] == "Rainy, 52°F"
    assert len(state["traffic_alerts"]) == 1


# ── Factory constructors ──────────────────────────────────────────────────────

def test_from_sensor_event():
    p = ContextPacket.from_sensor_event(pir_triggered=True, uptime_ms=12345)
    assert p.sensor.pir_triggered is True
    assert p.phase == "input"


def test_from_user_voice_detects_question():
    emo = EmotionScores(stress=0.2, calm=0.8)
    p = ContextPacket.from_user_voice("What's ahead of me?", emo)
    assert p.user_voice.has_question is True
    assert p.user_voice.transcript == "What's ahead of me?"


def test_from_user_voice_no_question():
    emo = EmotionScores()
    p = ContextPacket.from_user_voice("Stop the agent.", emo)
    assert p.user_voice.has_question is False


# ── Unique request IDs ────────────────────────────────────────────────────────

def test_unique_request_ids():
    ids = {ContextPacket().request_id for _ in range(20)}
    assert len(ids) == 20  # All unique
