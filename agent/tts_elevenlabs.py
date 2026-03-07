"""
tts_elevenlabs.py — ElevenLabs TTS integration for the Blind Driver Safety Agent.

Uses the eleven_flash_v2_5 model for ultra-low-latency (~75ms) audio generation,
then plays via pygame.mixer for immediacy. Voice settings adapt to alert urgency:
  calm    — stable, slower delivery
  caution — slightly elevated pace
  danger  — maximum clarity, fast
"""

import io
import logging
import threading
from typing import Literal

logger = logging.getLogger(__name__)

UrgencyLevel = Literal["calm", "caution", "danger"]

# Voice stability/clarity settings per urgency level
VOICE_SETTINGS = {
    "calm": {
        "stability": 0.80,
        "similarity_boost": 0.75,
        "style": 0.10,
        "use_speaker_boost": False,
    },
    "caution": {
        "stability": 0.65,
        "similarity_boost": 0.80,
        "style": 0.25,
        "use_speaker_boost": True,
    },
    "danger": {
        "stability": 0.45,
        "similarity_boost": 0.90,
        "style": 0.50,
        "use_speaker_boost": True,
    },
}


class TTSClient:
    """
    Wraps ElevenLabs SDK to convert text → speech with urgency-aware voice tuning.
    Audio is streamed to system speakers via pygame. Thread-safe.
    """

    def __init__(self, api_key: str, voice_id: str, mock: bool = False):
        self.api_key = api_key
        self.voice_id = voice_id
        self.mock = mock
        self._lock = threading.Lock()
        self._client = None
        self._audio_ready = False

        if not mock:
            self._init_elevenlabs()
            self._init_audio()

    def _init_elevenlabs(self):
        try:
            from elevenlabs.client import ElevenLabs
            self._client = ElevenLabs(api_key=self.api_key)
            logger.info("[TTS] ElevenLabs client initialized ✅")
        except ImportError:
            logger.error("[TTS] elevenlabs package not installed. Run: pip install elevenlabs")
            self.mock = True

    def _init_audio(self):
        try:
            import pygame
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=1, buffer=512)
            pygame.mixer.init()
            self._audio_ready = True
            logger.info("[TTS] pygame.mixer initialized ✅")
        except Exception as e:
            logger.warning(f"[TTS] Audio init failed: {e} — audio will be skipped")
            self._audio_ready = False

    # ── Public API ────────────────────────────────────────────────────────────

    def speak(self, text: str, urgency: UrgencyLevel = "calm", blocking: bool = False):
        """
        Convert text to speech and play it.
        Non-blocking by default (fires in a background thread).
        """
        if not text:
            return

        if blocking:
            self._speak_sync(text, urgency)
        else:
            t = threading.Thread(target=self._speak_sync, args=(text, urgency), daemon=True)
            t.start()

    def _speak_sync(self, text: str, urgency: UrgencyLevel):
        with self._lock:
            if self.mock:
                print(f"[MockTTS] 🔊 [{urgency.upper()}]: {text}")
                return

            try:
                self._generate_and_play(text, urgency)
            except Exception as e:
                logger.error(f"[TTS] speak failed: {e}")
                print(f"[TTS FALLBACK] [{urgency.upper()}]: {text}")

    def _generate_and_play(self, text: str, urgency: UrgencyLevel):
        from elevenlabs import VoiceSettings
        import pygame

        settings = VOICE_SETTINGS[urgency]
        voice_settings = VoiceSettings(
            stability=settings["stability"],
            similarity_boost=settings["similarity_boost"],
            style=settings["style"],
            use_speaker_boost=settings["use_speaker_boost"],
        )

        logger.debug(f"[TTS] Generating speech: '{text[:60]}...' urgency={urgency}")

        # Stream audio bytes from ElevenLabs Flash model
        audio_stream = self._client.text_to_speech.convert(
            voice_id=self.voice_id,
            text=text,
            model_id="eleven_flash_v2_5",
            voice_settings=voice_settings,
            output_format="mp3_44100_128",
        )

        # Collect streamed bytes
        audio_bytes = b"".join(chunk for chunk in audio_stream if chunk)

        if not audio_bytes:
            logger.warning("[TTS] Received empty audio — skipping playback")
            return

        # Play via pygame
        if self._audio_ready:
            audio_file = io.BytesIO(audio_bytes)
            pygame.mixer.music.load(audio_file, "mp3")
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                import time
                time.sleep(0.05)
        else:
            logger.info(f"[TTS] (no audio output) '{text}'")
