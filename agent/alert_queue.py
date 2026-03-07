"""
alert_queue.py — Priority alert queue for blind pedestrian navigation.

Combines Gemini Vision scene descriptions with HC-SR04 distance sensor data
into a sorted list of alerts. The queue is ordered by urgency so ElevenLabs
always reads the most important alert first.

Priority order:
  1. DANGER   — car very close (distance sensor ≤60cm OR Gemini danger object)
  2. CAUTION  — moderate hazard (Gemini caution / red light / don't walk)
  3. CALM     — informational (safe to cross, path clear)

Each alert is a dict:
  {
    "priority": int,       # 1=highest, 3=lowest
    "urgency":  str,       # "danger" | "caution" | "calm"
    "text":     str,       # full speech text for ElevenLabs
    "haptic":   str,       # "rapid" | "pulse" | "long" | "calm"
    "source":   str,       # "distance" | "gemini" | "crossing"
  }
"""

from __future__ import annotations
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.gemini_vision import GeminiScene

logger = logging.getLogger(__name__)

# ── Priority constants ─────────────────────────────────────────────────────────
P_DANGER  = 1
P_CAUTION = 2
P_CALM    = 3

HAPTIC = {"danger": "rapid", "caution": "pulse", "calm": "long"}


def _alert(urgency: str, text: str, source: str) -> dict:
    priority = {
        "danger":  P_DANGER,
        "caution": P_CAUTION,
        "calm":    P_CALM,
    }.get(urgency, P_CAUTION)
    return {
        "priority": priority,
        "urgency":  urgency,
        "text":     text,
        "haptic":   HAPTIC.get(urgency, "pulse"),
        "source":   source,
    }


def build_alert_queue(
    scene: "GeminiScene",
    distance_cm: Optional[float],
) -> list[dict]:
    """
    Fuse Gemini scene + distance sensor reading into a priority-sorted alert list.

    Returns a list sorted by priority (1 = most urgent first).
    ElevenLabs reads alerts[0].text, then alerts[1].text, etc.
    """
    alerts: list[dict] = []

    # ── Distance sensor (always highest priority if very close) ───────────────
    if distance_cm is not None:
        if distance_cm <= 60:
            alerts.append(_alert(
                "danger",
                f"Warning! Object {distance_cm:.0f} centimeters directly ahead. Stop immediately.",
                "distance",
            ))
        elif distance_cm <= 150:
            dist_ft = distance_cm / 30.48
            alerts.append(_alert(
                "caution",
                f"Object about {dist_ft:.1f} feet ahead. Slow down.",
                "distance",
            ))
        # Beyond 150cm: no distance alert (Gemini handles the scene description)

    # ── Gemini danger objects ─────────────────────────────────────────────────
    for obj in (scene.objects if scene else []):
        if obj.urgency == "danger":
            alerts.append(_alert("danger", obj.speech, "gemini"))

    # ── Crossing signal ───────────────────────────────────────────────────────
    if scene:
        if scene.walk_signal is True and scene.safe_to_cross:
            alerts.append(_alert(
                "calm",
                "Walk signal is on. Path appears clear. Safe to cross now.",
                "crossing",
            ))
        elif scene.walk_signal is False:
            alerts.append(_alert(
                "caution",
                "Don't walk signal active. Wait at the curb.",
                "crossing",
            ))
        elif scene.traffic_light == "red":
            alerts.append(_alert("caution", "Red light. Do not cross.", "crossing"))
        elif scene.traffic_light == "green" and not scene.safe_to_cross:
            alerts.append(_alert(
                "caution",
                "Green light but traffic may still be present. Proceed with caution.",
                "crossing",
            ))

        # ── Gemini caution objects ─────────────────────────────────────────────
        for obj in scene.objects:
            if obj.urgency == "caution":
                alerts.append(_alert("caution", obj.speech, "gemini"))

        # ── All clear ─────────────────────────────────────────────────────────
        if scene.safe_to_cross and not alerts:
            alerts.append(_alert("calm", "Path is clear. Safe to cross.", "crossing"))

    # ── Sort by priority (danger first) ──────────────────────────────────────
    alerts.sort(key=lambda a: a["priority"])

    logger.debug(f"[AlertQueue] {len(alerts)} alerts: "
                 + ", ".join(f"[{a['urgency']}] {a['text'][:40]}" for a in alerts))
    return alerts
