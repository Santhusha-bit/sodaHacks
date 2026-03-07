"""
hume_client.py — Hume EVI (Empathic Voice Interface) integration.

Uses Hume's speech prosody + vocal expression models to detect driver
emotional state in real-time (stress, fear, drowsiness).

When driver stress or fear exceeds configured thresholds, the agent
escalates to higher urgency alerts automatically.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from hume import AsyncHumeClient
    from hume.empathic_voice.types import (
        UserMessage,
        AssistantMessage,
        ToolCallMessage,
    )
    HUME_AVAILABLE = True
except ImportError:
    logger.warning("[Hume] hume SDK not installed — running in mock mode.")
    HUME_AVAILABLE = False


@dataclass
class EmotionReading:
    """Snapshot of detected driver emotional state."""
    stress: float = 0.0       # 0.0–1.0 (maps from Hume's anxiety/distress)
    calm: float = 1.0         # 0.0–1.0
    fear: float = 0.0         # 0.0–1.0
    sadness: float = 0.0      # 0.0–1.0 (potential drowsiness proxy)
    confidence: float = 0.0   # how confident the reading is
    raw_emotions: dict = field(default_factory=dict)

    @property
    def is_elevated(self) -> bool:
        """True if driver appears stressed or afraid."""
        return self.stress > 0.5 or self.fear > 0.5

    @property
    def summary(self) -> str:
        dominant = max(
            {"stress": self.stress, "calm": self.calm, "fear": self.fear}.items(),
            key=lambda x: x[1],
        )
        return f"{dominant[0]} ({dominant[1]:.2f})"


# Mapping from Hume's 48 emotion labels → our simplified model
STRESS_EMOTIONS = {"Anxiety", "Distress", "Nervousness", "Fear"}
CALM_EMOTIONS = {"Calmness", "Contentment", "Relief", "Satisfaction"}
FEAR_EMOTIONS = {"Fear", "Horror", "Dread"}


def _parse_emotions(predictions: list) -> EmotionReading:
    """Convert Hume prediction scores into our EmotionReading."""
    raw = {}
    for pred in predictions:
        name = getattr(pred, "name", None) or pred.get("name", "")
        score = getattr(pred, "score", None) or pred.get("score", 0.0)
        raw[name] = float(score)

    stress = max((raw.get(e, 0.0) for e in STRESS_EMOTIONS), default=0.0)
    calm = max((raw.get(e, 0.0) for e in CALM_EMOTIONS), default=0.0)
    fear = max((raw.get(e, 0.0) for e in FEAR_EMOTIONS), default=0.0)
    sadness = raw.get("Sadness", 0.0)
    confidence = sum(raw.values()) / len(raw) if raw else 0.0

    return EmotionReading(
        stress=stress,
        calm=calm,
        fear=fear,
        sadness=sadness,
        confidence=confidence,
        raw_emotions=raw,
    )


class HumeClient:
    """
    Manages a Hume Expression Measurement session.
    Processes audio snippets (from microphone) to extract emotional state.
    """

    def __init__(self, api_key: str, config_id: Optional[str] = None, mock: bool = False):
        self.api_key = api_key
        self.config_id = config_id
        self.mock = mock or not HUME_AVAILABLE
        self._last_reading = EmotionReading()
        self._client = None

    def get_last_reading(self) -> EmotionReading:
        """Return the most recent emotion reading (non-blocking)."""
        return self._last_reading

    async def analyze_audio_file(self, filepath: str) -> EmotionReading:
        """
        Submit an audio file to Hume Expression Measurement API.
        Returns EmotionReading. Use for batch/offline analysis.
        """
        if self.mock:
            return self._mock_reading()

        try:
            client = AsyncHumeClient(api_key=self.api_key)

            from hume.expression_measurement.batch import Models, Prosody
            from hume.expression_measurement.batch.types import InferenceBaseRequest

            job = await client.expression_measurement.batch.start_inference_job_from_local_file(
                file=[filepath],
                models=InferenceBaseRequest(prosody=Prosody()),
            )
            await job.await_complete()
            predictions = await job.get_predictions()

            # Parse first file's first group's first prediction
            for file_pred in predictions:
                for group in file_pred.results.predictions:
                    for frame in group.models.prosody.grouped_predictions:
                        for pred in frame.predictions:
                            reading = _parse_emotions(pred.emotions)
                            self._last_reading = reading
                            logger.info(f"[Hume] Emotion: {reading.summary}")
                            return reading
        except Exception as e:
            logger.error(f"[Hume] Analysis failed: {e}")

        return self._last_reading

    def _mock_reading(self) -> EmotionReading:
        """Return a simulated calm reading for testing."""
        import random
        stress = random.uniform(0.0, 0.3)
        reading = EmotionReading(
            stress=stress,
            calm=1.0 - stress,
            fear=random.uniform(0.0, 0.15),
        )
        logger.info(f"[MockHume] 🎭 Simulated emotion: {reading.summary}")
        self._last_reading = reading
        return reading
