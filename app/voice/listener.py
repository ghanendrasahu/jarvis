from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.voice.wake_word_detector import (
    PhraseMatchingDetectorForIntegration,
    VadAwareDetector,
)

logger = get_logger("voice")


@dataclass
class ListenResult:
    transcript: str | None
    issue: str | None = None


class _VadGate:
    """Optional voice-activity gate so STT APIs are only called on real speech."""

    def __init__(self) -> None:
        self._vad = None
        self._available = False
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(2)
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def has_speech(self, audio) -> bool:
        if not self._available or self._vad is None:
            return True
        try:
            samples = audio.get_raw_data(convert_rate=16000, convert_width=2)
            frame_size = int(16000 * 0.02) * 2
            frames = [samples[i : i + frame_size] for i in range(0, len(samples) - frame_size + 1, frame_size)]
            if not frames:
                return False
            speech_frames = sum(1 for frame in frames if self._vad.is_speech(frame, 16000))
            return speech_frames / len(frames) >= 0.25
        except Exception:
            return True


class VoiceListener:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._recognizer: Any = None
        self._backend = "groq_whisper" if bool(settings.groq_api_key) else "google_web_speech"
        self._device_index: int | None = None
        self._mic_lock = threading.RLock()
        self._microphone_names_cache: list[str] | None = None
        self._device_name_cache: dict[int, str] = {}
        self._last_ambient_adjust_at: float | None = None
        self._vad = _VadGate()
        self._groq_client: Any = None

        # Wake-word detector: real model not available, use phrase-matching
        # as a partial fallback, wrapped in VadAwareDetector so silence
        # suppresses detection.
        self._wake_word_detector = VadAwareDetector(
            PhraseMatchingDetectorForIntegration(settings.wake_word),
            self._vad,
        )

    def _get_recognizer(self) -> Any:
        if self._recognizer is None:
            import speech_recognition as sr

            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = 300
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.pause_threshold = 0.8
        return self._recognizer

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def device_index(self) -> int | None:
        with self._mic_lock:
            return self._device_index

    def set_device_index(self, index: int | None) -> None:
        with self._mic_lock:
            self._device_index = index

    def list_microphones(self) -> list[str]:
        """Return microphone names, caching the PyAudio enumeration."""
        with self._mic_lock:
            if self._microphone_names_cache is not None:
                return list(self._microphone_names_cache)

            import speech_recognition as sr

            self._microphone_names_cache = [name for name in sr.Microphone.list_microphone_names()]
            self._device_name_cache = {
                index: name for index, name in enumerate(self._microphone_names_cache)
            }
            return list(self._microphone_names_cache)

    def get_device_name(self) -> str | None:
        with self._mic_lock:
            if self._device_index is None:
                return None

            cached = self._device_name_cache.get(self._device_index)
            if cached is not None:
                return cached

            if self._microphone_names_cache is None:
                return None
            if self._device_index < 0 or self._device_index >= len(self._microphone_names_cache):
                return None

            name = self._microphone_names_cache[self._device_index]
            self._device_name_cache[self._device_index] = name
            return name

    @staticmethod
    def _score_mic_name(name: str) -> int:
        lowered = name.lower()
        score = 0
        if "microphone" in lowered:
            score += 50
        if "realtek" in lowered:
            score += 25
        if "headset" in lowered:
            score += 15
        if "usb" in lowered:
            score += 15
        if "airpods" in lowered or "earbuds" in lowered:
            score += 10
        if "hands-free" in lowered:
            score -= 25
        if "output" in lowered:
            score -= 30
        if "stereo mix" in lowered:
            score -= 40
        if "sound mapper" in lowered or "primary sound" in lowered:
            score -= 30
        return score

    def _auto_pick_microphone(self, names: Iterable[str]) -> int | None:
        with self._mic_lock:
            import pyaudio

            names = list(names)
            scored = [(self._score_mic_name(name), index, name) for index, name in enumerate(names)]
            if not scored:
                return None

            scored.sort(key=lambda x: x[0], reverse=True)

            p = pyaudio.PyAudio()
            try:
                for _score, index, _name in scored:
                    if _score < 10:
                        break
                    try:
                        info = p.get_device_info_by_index(index)
                        if int(info.get("maxInputChannels", 0)) <= 0:
                            continue

                        s = p.open(
                            input_device_index=index,
                            channels=1,
                            format=pyaudio.paInt16,
                            rate=16000,
                            frames_per_buffer=1024,
                            input=True,
                        )
                        s.close()
                        if 0 <= index < len(names):
                            self._device_name_cache[index] = names[index]
                        return index
                    except Exception:
                        continue
            finally:
                p.terminate()

            return None

    def listen_once(self, stop_event: threading.Event | None = None) -> ListenResult:
        import speech_recognition as sr

        recognizer = self._get_recognizer()

        with self._mic_lock:
            if self._device_index is None:
                try:
                    names = self.list_microphones()
                    self._device_index = self._auto_pick_microphone(names)
                except Exception:
                    self._device_index = None

            mic = sr.Microphone(device_index=self._device_index)

            try:
                source = mic.__enter__()
            except Exception:
                return ListenResult(
                    transcript=None,
                    issue="microphone_init_failed",
                )

            try:
                if stop_event is not None and stop_event.is_set():
                    return ListenResult(
                        transcript=None,
                        issue="stopped",
                    )

                now = time.time()
                interval = max(
                    3,
                    int(self.settings.speech_ambient_adjust_interval_seconds),
                )
                should_adjust = self._last_ambient_adjust_at is None or (now - self._last_ambient_adjust_at) >= interval

                if should_adjust:
                    recognizer.adjust_for_ambient_noise(
                        source,
                        duration=max(
                            0.05,
                            float(self.settings.speech_ambient_adjust_duration_seconds),
                        ),
                    )
                    self._last_ambient_adjust_at = now

                if stop_event is not None and stop_event.is_set():
                    return ListenResult(
                        transcript=None,
                        issue="stopped",
                    )

                try:
                    audio = recognizer.listen(
                        source,
                        timeout=min(self.settings.speech_timeout_seconds, 2),
                        phrase_time_limit=self.settings.phrase_time_limit_seconds,
                    )
                except sr.WaitTimeoutError:
                    if stop_event is not None and stop_event.is_set():
                        return ListenResult(
                            transcript=None,
                            issue="stopped",
                        )
                    return ListenResult(
                        transcript=None,
                        issue="timeout",
                    )
            finally:
                try:
                    mic.__exit__(None, None, None)
                except Exception:
                    pass

        if not self._vad.has_speech(audio):
            return ListenResult(
                transcript=None,
                issue="no_speech",
            )

        try:
            if self.settings.groq_api_key:
                transcript = self._transcribe_groq(audio)
                return ListenResult(
                    transcript=transcript.strip(),
                    issue=None,
                )

            if self.settings.openai_api_key and hasattr(
                recognizer,
                "recognize_whisper_api",
            ):
                transcript = recognizer.recognize_whisper_api(
                    audio,
                    api_key=self.settings.openai_api_key,
                )
                return ListenResult(
                    transcript=transcript.strip(),
                    issue=None,
                )

            transcript = recognizer.recognize_google(audio)
            return ListenResult(
                transcript=transcript.strip(),
                issue=None,
            )

        except sr.UnknownValueError:
            return ListenResult(
                transcript=None,
                issue="unknown_audio",
            )
        except sr.RequestError as exc:
            return ListenResult(
                transcript=None,
                issue=f"request_error: {exc}",
            )
        except Exception as exc:
            return ListenResult(
                transcript=None,
                issue=f"listener_error: {exc}",
            )

    def _transcribe_groq(self, audio) -> str:
        """Transcribe via Groq's free-tier Whisper (OpenAI-compatible endpoint)."""
        import tempfile

        if self._groq_client is None:
            from openai import OpenAI

            self._groq_client = OpenAI(
                api_key=self.settings.groq_api_key,
                base_url=self.settings.groq_base_url,
            )

        path = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False,
            ) as handle:
                handle.write(audio.get_wav_data())
                path = handle.name

            with open(path, "rb") as handle:
                result = self._groq_client.audio.transcriptions.create(
                    model="whisper-large-v3-turbo",
                    file=handle,
                )

            return result.text or ""

        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
