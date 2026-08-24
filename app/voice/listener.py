from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.nlu import build_stt_prompt, strip_prompt_echo

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
        # Active backend reflects what actually transcribed the last utterance;
        # None until the first successful STT call (status shows the default).
        self._active_backend: str | None = None
        # Local Qwen3-ASR state: created lazily on the voice worker thread;
        # a load failure latches for the session so every command does not
        # pay the (slow) load attempt again.
        self._qwen: Any = None
        self._qwen_load_failed: bool = False
        self._device_index: int | None = None
        # Plain-attribute snapshots readable without _mic_lock so GUI status
        # polls never contend with an active capture cycle (GIL-atomic reads).
        self._device_index_snapshot: int | None = None
        self._device_name_snapshot: str | None = None
        self._mic_lock = threading.RLock()
        self._microphone_names_cache: list[str] | None = None
        self._device_name_cache: dict[int, str] = {}
        self._last_ambient_adjust_at: float | None = None
        self._vad = _VadGate()
        self._groq_client: Any = None
        self._last_listen_end_at: float = 0.0

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
        """STT backend currently in effect (post-fallback), for status display."""
        if self._active_backend == "qwen3":
            return "qwen3_local"
        if self._active_backend == "groq":
            return "groq_whisper"
        if self._active_backend == "openai":
            return "openai_whisper"
        if self._active_backend == "google":
            return "google_web_speech"
        return self._backend

    def _publish_device_snapshot_locked(self) -> None:
        """Refresh the lock-free snapshots. Callers must hold _mic_lock."""
        self._device_index_snapshot = self._device_index
        self._device_name_snapshot = (
            self._device_name_cache.get(self._device_index) if self._device_index is not None else None
        )

    @property
    def device_index(self) -> int | None:
        return self._device_index_snapshot

    def set_device_index(self, index: int | None) -> None:
        with self._mic_lock:
            self._device_index = index
            self._publish_device_snapshot_locked()

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
            self._publish_device_snapshot_locked()
            return list(self._microphone_names_cache)

    def get_device_name(self) -> str | None:
        return self._device_name_snapshot

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
        audio = None
        try:
            with self._mic_lock:
                if stop_event is not None and stop_event.is_set():
                    return ListenResult(transcript=None, issue="stopped")
                if self._device_index is None:
                    try:
                        names = self.list_microphones()
                        self._device_index = self._auto_pick_microphone(names)
                    except Exception:
                        self._device_index = None
                    self._publish_device_snapshot_locked()

                mic = sr.Microphone(device_index=self._device_index)

                try:
                    source = mic.__enter__()
                except Exception:
                    return ListenResult(transcript=None, issue="microphone_init_failed")

                try:
                    if stop_event is not None and stop_event.is_set():
                        return ListenResult(transcript=None, issue="stopped")

                    now = time.time()
                    interval = max(3, int(self.settings.speech_ambient_adjust_interval_seconds))
                    should_adjust = self._last_ambient_adjust_at is None or (now - self._last_ambient_adjust_at) >= interval
                    if should_adjust:
                        recognizer.adjust_for_ambient_noise(
                            source,
                            duration=max(0.05, float(self.settings.speech_ambient_adjust_duration_seconds)),
                        )
                        self._last_ambient_adjust_at = now

                    if stop_event is not None and stop_event.is_set():
                        return ListenResult(transcript=None, issue="stopped")

                    try:
                        audio = recognizer.listen(
                            source,
                            timeout=min(self.settings.speech_timeout_seconds, 2),
                            phrase_time_limit=self.settings.phrase_time_limit_seconds,
                        )
                    except sr.WaitTimeoutError:
                        if stop_event is not None and stop_event.is_set():
                            return ListenResult(transcript=None, issue="stopped")
                        return ListenResult(transcript=None, issue="timeout")
                finally:
                    try:
                        mic.__exit__(None, None, None)
                    except Exception:
                        pass

            if not self._vad.has_speech(audio):
                return ListenResult(transcript=None, issue="no_speech")

            chain_result = self._run_stt_chain(audio, recognizer)
            if chain_result.issue is not None:
                return chain_result
            transcript = chain_result.transcript or ""

            # Strip STT-prompt scaffolding echoed into the transcript so it can
            # never become an executable command.
            transcript = strip_prompt_echo(transcript)
            if not transcript:
                return ListenResult(transcript=None, issue="prompt_echo")

            if not self._is_transcript_plausible(transcript, self.settings.voice_min_transcript_chars):
                return ListenResult(transcript=None, issue="garbled_transcript")
            return ListenResult(transcript=transcript, issue=None)

        except sr.UnknownValueError:
            return ListenResult(transcript=None, issue="unknown_audio")
        except sr.RequestError as exc:
            return ListenResult(transcript=None, issue=f"request_error: {exc}")
        except Exception as exc:
            return ListenResult(transcript=None, issue=f"listener_error: {exc}")
        finally:
            self._last_listen_end_at = time.time()

    def _run_stt_chain(self, audio, recognizer) -> ListenResult:
        """Transcribe via the configured backend chain, falling back on failure.

        Order comes from VOICE_STT_BACKEND / VOICE_STT_FALLBACK (qwen3 -> groq by
        default).         Duck-typed settings without those fields keep the exact legacy
        behavior (groq if a key exists, else openai/google web speech).
        """
        import speech_recognition as sr

        primary = str(getattr(self.settings, "voice_stt_backend", "") or "").strip().lower()
        fallback = str(getattr(self.settings, "voice_stt_fallback", "") or "").strip().lower()
        groq_available = bool(self.settings.groq_api_key)

        order: list[str] = []
        if primary == "qwen3":
            order.append("qwen3")
            if fallback == "groq" and groq_available:
                order.append("groq")
        elif primary == "groq":
            if groq_available:
                order.append("groq")
            if fallback == "qwen3":
                order.append("qwen3")
        elif primary == "openai":
            order.append("openai")
        elif primary == "google":
            order.append("google")
        else:
            # Legacy default when no explicit backend is configured.
            if groq_available:
                order.append("groq")
            elif self.settings.openai_api_key and hasattr(recognizer, "recognize_whisper_api"):
                order.append("openai")
            else:
                order.append("google")

        # Never strand the user with no usable cloud option: if the configured
        # chain produced no cloud backend at all, append the legacy tail.
        if not any(name in {"groq", "openai", "google"} for name in order):
            if groq_available:
                order.append("groq")
            elif self.settings.openai_api_key and hasattr(recognizer, "recognize_whisper_api"):
                order.append("openai")
            else:
                order.append("google")

        attempted: list[str] = []
        for name in order:
            attempted.append(name)
            try:
                if name == "qwen3":
                    text = self._transcribe_qwen(audio)
                elif name == "groq":
                    verbose = self._transcribe_groq(audio)
                    text = (getattr(verbose, "text", "") or "").strip()
                    confidence = self._confidence_from_verbose(verbose)
                    if confidence is not None and not self._is_confidence_acceptable(*confidence):
                        # A valid low-confidence result is not a backend failure;
                        # surface it exactly as the legacy path did.
                        return ListenResult(transcript=None, issue="low_confidence_transcript")
                elif name == "openai":
                    text = recognizer.recognize_whisper_api(
                        audio, api_key=self.settings.openai_api_key
                    ).strip()
                else:
                    text = recognizer.recognize_google(audio).strip()

                if self._active_backend != name:
                    logger.info("[voice] STT backend active: %s", name)
                self._active_backend = name
                return ListenResult(transcript=text.strip(), issue=None)
            except sr.UnknownValueError:
                raise  # preserve legacy unknown_audio handling for cloud APIs
            except Exception as exc:
                logger.warning("[voice] STT backend '%s' failed (%s); trying next", name, exc)
                continue

        return ListenResult(
            transcript=None,
            issue=f"request_error: all stt backends failed [{','.join(attempted)}]",
        )

    def _get_qwen(self) -> Any:
        if self._qwen is None:
            from app.voice.qwen_stt import DEFAULT_QWEN_STT_MODEL_ID, Qwen3AsrTranscriber

            model_id = (
                str(getattr(self.settings, "qwen_stt_model_id", "") or "").strip()
                or DEFAULT_QWEN_STT_MODEL_ID
            )
            self._qwen = Qwen3AsrTranscriber(model_id)
        return self._qwen

    def _transcribe_qwen(self, audio) -> str:
        """Local Qwen3-ASR inference; lazy-loads the model on first call."""
        if self._qwen_load_failed:
            raise RuntimeError("qwen3 unavailable earlier this session")
        qwen = self._get_qwen()
        was_loaded = qwen.loaded
        try:
            wav_bytes = audio.get_wav_data()
            language = str(getattr(self.settings, "stt_language", "") or "")
            return qwen.transcribe_wav_bytes(wav_bytes, language_code=language)
        except Exception as exc:
            if not was_loaded and not qwen.loaded:
                # Load-phase failure (missing deps/weights, OOM): latch off so
                # the fallback takes over instantly for the rest of the session.
                self._qwen_load_failed = True
                logger.error("[voice] qwen3 STT failed to load (%s); using %s fallback", exc,
                             getattr(self.settings, "voice_stt_fallback", "groq"))
            else:
                logger.warning("[voice] qwen3 transcription error: %s", exc)
            raise

    @staticmethod
    def _is_transcript_plausible(transcript: str, min_chars: int = 2) -> bool:
        text = " ".join(transcript.split()).strip()
        if len(text) < max(1, min_chars):
            return False
        words = [word for word in re.findall(r"[A-Za-z0-9']+", text)]
        if len(words) == 0:
            return False
        if len(words) == 1 and len(words[0]) < 4:
            return False
        alpha = sum(1 for ch in text if ch.isalpha())
        if alpha < max(2, len(text) // 4):
            return False
        if len(text) > 4 and len(words) <= 1 and not re.search(r"\d", text):
            return False
        return True

    def _transcribe_groq(self, audio) -> Any:
        """Transcribe via Groq's free-tier Whisper (OpenAI-compatible endpoint).

        Tuned for accent robustness: explicit language hint plus a dynamic
        initial_prompt biased toward JARVIS's real command vocabulary.
        """
        import tempfile

        if self._groq_client is None:
            from openai import OpenAI

            # Explicit timeout + capped retries: without these the SDK waits up
            # to 600s per attempt and the voice loop goes deaf for minutes.
            self._groq_client = OpenAI(
                api_key=self.settings.groq_api_key,
                base_url=self.settings.groq_base_url,
                timeout=30.0,
                max_retries=1,
            )

        path = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False,
            ) as handle:
                handle.write(audio.get_wav_data())
                path = handle.name

            request_kwargs: dict[str, Any] = {"response_format": "verbose_json"}
            language = str(getattr(self.settings, "stt_language", "") or "").strip()
            if language:
                request_kwargs["language"] = language
            if bool(getattr(self.settings, "stt_prompt_bias", True)):
                request_kwargs["prompt"] = build_stt_prompt(self.settings.wake_word)

            with open(path, "rb") as audio_file:
                result = self._groq_client.audio.transcriptions.create(
                    model="whisper-large-v3-turbo",
                    file=audio_file,
                    **request_kwargs,
                )

            return result

        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    @staticmethod
    def _confidence_from_verbose(result: Any) -> tuple[float, float] | None:
        """Return (weighted avg_logprob, max no_speech_prob) from verbose_json."""
        segments = getattr(result, "segments", None)
        if not segments:
            return None
        total_weight = 0.0
        logprob_sum = 0.0
        max_no_speech = 0.0
        for segment in segments:
            if isinstance(segment, dict):
                logprob = segment.get("avg_logprob")
                no_speech = segment.get("no_speech_prob")
                duration = float(segment.get("end") or 0) - float(segment.get("start") or 0)
            else:
                logprob = getattr(segment, "avg_logprob", None)
                no_speech = getattr(segment, "no_speech_prob", None)
                duration = float(getattr(segment, "end", 0) or 0) - float(getattr(segment, "start", 0) or 0)
            if logprob is None:
                continue
            weight = max(0.001, duration)
            logprob_sum += float(logprob) * weight
            total_weight += weight
            try:
                max_no_speech = max(max_no_speech, float(no_speech or 0.0))
            except (TypeError, ValueError):
                continue
        if total_weight <= 0:
            return None
        return logprob_sum / total_weight, max_no_speech

    def _is_confidence_acceptable(self, avg_logprob: float, max_no_speech: float) -> bool:
        """Conservative garbage filter; ambiguous-but-real speech still flows on."""
        min_logprob = float(getattr(self.settings, "voice_min_avg_logprob", -1.0))
        max_no_speech_prob = float(getattr(self.settings, "voice_max_no_speech_prob", 0.9))
        return avg_logprob >= min_logprob and max_no_speech <= max_no_speech_prob
