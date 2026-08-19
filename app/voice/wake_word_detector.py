from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("wake_word")


class WakeWordResult:
    """Result of a wake-word detection attempt."""
    def __init__(self, detected: bool, confidence: float = 0.0, audio_segment_end: float | None = None):
        self.detected = detected
        self.confidence = confidence
        self.audio_segment_end = audio_segment_end

    def __bool__(self) -> bool:
        return self.detected


class WakeWordDetector:
    """Abstraction for wake-word detection.

    Subclasses must implement the ``detect`` method.
    The base class provides timing and integration hooks.

    Hardware/model-dependent implementations should be provided
    by third-party packages (Porcupine, Snowboy, etc.). If such a
    package is not available, the ``PhraseMatchingDetector`` below
    may be used as a partial fallback — but it must be clearly
    marked as such.
    """

    def detect(self, audio, sample_rate: int = 16000) -> WakeWordResult:
        """Detect wake word in *audio* at *sample_rate*.

        Returns a ``WakeWordResult`` whose ``.detected`` flag is
        True when the wake word is found.

        Raises ``NotImplementedError`` if the subclass cannot perform
        detection.  Concrete subclasses must override this method.
        """
        raise NotImplementedError("WakeWordDetector subclasses must implement detect()")

    def listen_cycle(self, audio, sample_rate: int = 16000) -> WakeWordResult | None:
        """Run one listen cycle; returns result or None on error."""
        try:
            return self.detect(audio, sample_rate)
        except Exception as exc:
            logger.exception("[wake_word] detection error: %s", exc)
            return None


class PhraseMatchingDetector(WakeWordDetector):
    """Partial: detects wake word by checking if the phrase appears
    in the transcript.  This is NOT a real hotword model — it is
    included only as a convenience while a real implementation is
    integrated.  Do NOT claim full wake-word support when using this
    detector."""

    def __init__(self, wake_phrase: str = "jarvis") -> None:
        self.wake_phrase = wake_phrase.lower().strip()

    def detect(self, audio, sample_rate: int = 16000) -> WakeWordResult:
        """In a real implementation *audio* would be examined.
        Here we return ``detected=False`` to avoid faking wake-word
        support, and document the phrase-matching approach as a
        partial convenience."""
        # NOTE: we explicitly do NOT do phrase-matching here so
        # that callers are not misled into thinking a real hotword
        # is active.  The transcript-based check lives in the voice
        # loop (``_extract_command``) where it belongs.
        return WakeWordResult(detected=False, confidence=0.0)


# ------------------------------------------------------------------
# Convenience: a detector that *does* do phrase-matching for
# immediate use while a real model is being integrated.
# ------------------------------------------------------------------
# WARNING: This is NOT a real wake-word detector.  It is provided
# so that the voice loop can function during integration.  The
# master prompt warns: "Never claim full wake-word support without
# actual detection."  Replace this with a proper model before
# deployment.
# ------------------------------------------------------------------
class PhraseMatchingDetectorForIntegration(WakeWordDetector):
    """Partial convenience detector: returns True when the wake
    phrase appears in a *transcript* string.  The voice loop should
    call ``detect_transcript`` instead of ``detect`` when using
    this class."""

    def __init__(self, wake_phrase: str = "jarvis") -> None:
        self.wake_phrase = wake_phrase.lower().strip()

    def detect_transcript(self, transcript: str) -> bool:
        """Return True when *wake_phrase* is found in *transcript*."""
        return self.wake_phrase in transcript.lower()

    def detect(self, audio, sample_rate: int = 16000) -> WakeWordResult:
        """Placeholder: always False.  Use ``detect_transcript``
        with the raw transcript instead."""
        return WakeWordResult(detected=False, confidence=0.0)


# ------------------------------------------------------------------
# Real‑time VAD‑aware detector wrapper
# ------------------------------------------------------------------
class VadAwareDetector(WakeWordDetector):
    """Wrap any detector so that audio is first checked for voice
    activity before attempting wake-word detection.  This prevents
    false wakewords during silence and is essential for production
    quality."""

    def __init__(self, inner: WakeWordDetector, vad: Any | None = None) -> None:
        self.inner = inner
        self.vad = vad

    def detect(self, audio, sample_rate: int = 16000) -> WakeWordResult:
        if self.vad is not None and not self.vad.has_speech(audio):
            return WakeWordResult(detected=False, confidence=0.0)
        return self.inner.detect(audio, sample_rate)
