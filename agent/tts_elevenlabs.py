"""
tts_elevenlabs.py — ElevenLabs TTS for Blind Pedestrian Navigation Agent.

Uses eleven_flash_v2_5 (ultra-low latency, ~75ms) to convert alert text
into spoken audio that the blind pedestrian hears through speakers/earbuds
connected to the laptop or ESP32-S3-BOX-3.

Audio playback priority:
  1. ElevenLabs SDK play() helper — uses sounddevice (cross-platform, no SDL)
  2. pygame.mixer fallback if sounddevice unavailable
  3. Mock (just prints) if API key missing or in test mode

Voice settings adapt to urgency level so the pedestrian can tell urgency
from tone alone, even without understanding every word.
"""

import io
import logging
import threading
from typing import Literal

logger = logging.getLogger(__name__)

UrgencyLevel = Literal["calm", "caution", "danger"]

VOICE_SETTINGS = {
    "calm":    {"stability": 0.80, "similarity_boost": 0.75, "style": 0.10, "use_speaker_boost": False},
    "caution": {"stability": 0.65, "similarity_boost": 0.80, "style": 0.25, "use_speaker_boost": True},
    "danger":  {"stability": 0.45, "similarity_boost": 0.90, "style": 0.50, "use_speaker_boost": True},
}


class TTSClient:
    """
    Wraps ElevenLabs SDK to convert text → speech with urgency-aware voice tuning.
    Non-blocking: each call fires in a background thread.
    """

    def __init__(self, api_key: str, voice_id: str, mock: bool = False):
        self.api_key  = api_key
        self.voice_id = voice_id
        self.mock     = mock
        self._lock    = threading.Lock()
        self._client  = None
        self._play_fn = None   # the audio playback function we'll use

        if not mock:
            self._setup()

    def _setup(self):
        # ── 1. Init ElevenLabs SDK ────────────────────────────────────────────
        try:
            from elevenlabs.client import ElevenLabs
            self._client = ElevenLabs(api_key=self.api_key)
            logger.info("[TTS] ElevenLabs client initialized ✅")
        except Exception as e:
            logger.error(f"[TTS] ElevenLabs init failed: {e}")
            self.mock = True
            return

        # ── 2. Find audio playback backend ────────────────────────────────────
        # On macOS, prefer afplay (OS built-in) — avoids SDL2 conflict with cv2
        import subprocess, shutil
        if shutil.which("afplay"):
            self._play_fn = self._afplay
            logger.info("[TTS] Audio backend: macOS afplay ✅")
            return

        # Try ElevenLabs' own play() helper (uses sounddevice internally)
        try:
            from elevenlabs import play as _el_play
            import sounddevice  # noqa
            self._play_fn = _el_play
            logger.info("[TTS] Audio backend: elevenlabs.play (sounddevice) ✅")
            return
        except ImportError:
            pass

        # pygame fallback
        try:
            import pygame
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=1, buffer=1024)
            pygame.mixer.init()
            self._play_fn = self._pygame_play
            logger.info("[TTS] Audio backend: pygame.mixer ✅")
            return
        except Exception as e:
            logger.warning(f"[TTS] pygame.mixer failed: {e}")

        logger.warning("[TTS] No audio backend found — speech will be text-only")
        self._play_fn = self._text_only

    # ── Public API ────────────────────────────────────────────────────────────

    def speak(self, text: str, urgency: UrgencyLevel = "calm", blocking: bool = False):
        """Speak the text aloud. Non-blocking by default."""
        if not text:
            return
        if blocking:
            self._speak_sync(text, urgency)
        else:
            t = threading.Thread(target=self._speak_sync, args=(text, urgency), daemon=True)
            t.start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _speak_sync(self, text: str, urgency: UrgencyLevel):
        with self._lock:
            if self.mock:
                print(f"[MockTTS] 🔊 [{urgency.upper()}]: {text}")
                return
            try:
                self._generate_and_play(text, urgency)
            except Exception as e:
                err = str(e)
                if "quota_exceeded" in err or "401" in err:
                    # ElevenLabs credits exhausted — fall back to macOS say
                    self._say_fallback(text, urgency)
                else:
                    logger.error(f"[TTS] speak failed: {e}")
                    print(f"[TTS FALLBACK] 🔊 [{urgency.upper()}]: {text}")

    def _say_fallback(self, text: str, urgency: UrgencyLevel):
        """Use macOS built-in say command when ElevenLabs is unavailable."""
        import subprocess
        # Pick a voice that matches urgency: calm=Samantha, danger=Alex (faster)
        voice = {"danger": "Alex", "caution": "Samantha", "calm": "Samantha"}.get(urgency, "Samantha")
        rate  = {"danger": "220",  "caution": "180",       "calm": "160"}.get(urgency, "180")
        logger.info(f"[TTS] 🍎 macOS say [{urgency.upper()}]: {text[:60]}")
        subprocess.Popen(["say", "-v", voice, "-r", rate, text])


    def _generate_and_play(self, text: str, urgency: UrgencyLevel):
        from elevenlabs import VoiceSettings
        cfg = VOICE_SETTINGS[urgency]
        voice_settings = VoiceSettings(
            stability=cfg["stability"],
            similarity_boost=cfg["similarity_boost"],
            style=cfg["style"],
            use_speaker_boost=cfg["use_speaker_boost"],
        )

        logger.info(f"[TTS] 🗣  [{urgency.upper()}] {text[:80]}")

        # Get audio bytes from ElevenLabs Flash model
        audio_stream = self._client.text_to_speech.convert(
            voice_id=self.voice_id,
            text=text,
            model_id="eleven_flash_v2_5",
            voice_settings=voice_settings,
            output_format="mp3_44100_128",
        )
        audio_bytes = b"".join(chunk for chunk in audio_stream if chunk)

        if not audio_bytes:
            logger.warning("[TTS] Empty audio response from ElevenLabs")
            return

        if self._play_fn:
            self._play_fn(audio_bytes)

    def _pygame_play(self, audio_bytes: bytes):
        import pygame, io, time
        audio_file = io.BytesIO(audio_bytes)
        pygame.mixer.music.load(audio_file, "mp3")
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)

    def _afplay(self, audio_bytes: bytes):
        """macOS-native: write mp3 to temp file and play with afplay."""
        import subprocess, tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            subprocess.run(["afplay", tmp], check=True)
        finally:
            os.unlink(tmp)

    def _text_only(self, audio_bytes: bytes):
        pass  # Audio generated but no output device — log already emitted
