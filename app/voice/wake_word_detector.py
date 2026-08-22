from __future__ import annotations

from typing import Any

from app.core.nlu import fuzzy_wake_detected


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
    by third-party packages (Porcupine, Snowboy, etc.).
    """

    def detect(self, audio, sample_rate: int = 16000) -> WakeWordResult:
        """Detect wake word in *audio* at *sample_rate*.

        Returns a ``WakeWordResult`` whose ``.detected`` flag is
        True when the wake word is found.

        Raises ``NotImplementedError`` if the subclass cannot perform
        detection.  Concrete subclasses must override this method.
        """
        raise NotImplementedError("WakeWordDetector subclasses must implement detect()")


class PhraseMatchingDetectorForIntegration(WakeWordDetector):
    """Partial convenience detector: returns True when the wake
    phrase appears in a *transcript* string.  The voice loop should
    call ``detect_transcript`` instead of ``detect`` when using
    this class.

    Fuzzy matching accepts close spoken variants ("Jervis", "Jarvic")
    via RapidFuzz while keeping false positives low (configurable
    threshold).

    openWakeWord evaluation (Phase 4): an acoustic detector was
    considered but NOT integrated. Reasons: (1) openWakeWord ships no
    free pretrained "Jarvis" model - a custom model would have to be
    trained first; (2) continuous acoustic detection requires a
    persistent audio stream feeding both the detector and STT, which
    would replace the hardened SpeechRecognition mic lifecycle that the
    reliability phases just stabilized. The transcript-based fuzzy
    fallback stays as the production wake-word path; revisit if a
    custom model is trained."""

    def __init__(
        self,
        wake_phrase: str = "jarvis",
        fuzzy_enabled: bool = True,
        fuzzy_score: float = 82.0,
    ) -> None:
        self.wake_phrase = wake_phrase.lower().strip()
        self.fuzzy_enabled = fuzzy_enabled
        self.fuzzy_score = fuzzy_score

    def detect_transcript(self, transcript: str) -> bool:
        """Return True when *wake_phrase* (or a close variant) is found."""
        if not transcript:
            return False
        if self.wake_phrase in transcript.lower():
            return True
        if not self.fuzzy_enabled:
            return False
        return fuzzy_wake_detected(transcript, self.wake_phrase, self.fuzzy_score)

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
