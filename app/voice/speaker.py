from __future__ import annotations

import os
import subprocess
import threading
import time
from threading import Lock
from typing import Any

_AUDIO_OUTPUTS_TTL_SECONDS = 300.0


class Speaker:
    def __init__(
        self,
        enabled: bool = True,
        mode: str | None = None,
        kokoro_voice: str = "af_bella",
        kokoro_lang_code: str = "a",
        kokoro_speed: float = 1.0,
    ) -> None:
        self.enabled = enabled
        self._lock = Lock()
        self._engine = None
        self._engine_name = "uninitialized"
        self._last_error: str | None = None
        self._mode: str = mode or ("sapi" if os.name == "nt" else "pyttsx3")
        self._preferred_output_index: int | None = None
        self._kokoro_pipeline = None
        self._kokoro_voice = kokoro_voice
        self._kokoro_lang_code = kokoro_lang_code
        self._kokoro_speed = kokoro_speed
        self._audio_outputs_cache: dict[str, Any] | None = None
        self._audio_outputs_ts: float = 0.0
        # Audio-output enumeration uses SAPI COM; never do it on a caller that
        # may be the GUI thread - status() serves the cache and a daemon
        # thread refreshes it in the background.
        self._audio_refresh_lock = Lock()
        self._audio_refresh_inflight = False
        self._audio_refresh_last_attempt = 0.0

    def _ensure_pyttsx3_engine(self):
        """Initialize pyttsx3 lazily on the calling (TTS) thread, never at startup."""
        if self._engine is not None:
            return self._engine
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 190)
            self._engine = engine
            self._engine_name = "pyttsx3"
        except Exception as exc:
            self._last_error = f"pyttsx3_init_failed: {exc}"
            self._engine = None
        return self._engine

    def _init_kokoro(self) -> None:
        try:
            from kokoro import KPipeline  # type: ignore[import-untyped]

            self._kokoro_pipeline = KPipeline(lang_code=self._kokoro_lang_code)
            self._engine_name = "kokoro"
        except ImportError as exc:
            self._last_error = f"kokoro_import_failed: {exc} (pip install -e .[tts] + espeak-ng)"
            self._kokoro_pipeline = None
            self._mode = "sapi"
        except Exception as exc:
            self._last_error = f"kokoro_init_failed: {exc}"
            self._kokoro_pipeline = None
            self._mode = "sapi"

    def status(self) -> dict[str, Any]:
        audio_info = self._audio_outputs()
        return {
            "enabled": self.enabled,
            "engine": self._engine_name,
            "mode": self._mode,
            "preferred_output_index": self._preferred_output_index,
            "last_error": self._last_error,
            "audio_output_count": audio_info["count"],
            "audio_outputs": audio_info["devices"],
            "kokoro_available": self._kokoro_pipeline is not None,
            "kokoro_voice": self._kokoro_voice,
            "kokoro_lang_code": self._kokoro_lang_code,
        }

    def set_mode(self, mode: str) -> dict[str, Any]:
        normalized = mode.strip().lower()
        if normalized not in {"pyttsx3", "sapi", "kokoro"}:
            raise ValueError("mode must be one of: pyttsx3, sapi, kokoro")
        if normalized == "kokoro" and self._kokoro_pipeline is None:
            self._init_kokoro()
            if self._kokoro_pipeline is None:
                # Kokoro initialization failed; _init_kokoro already restored a
                # working fallback mode. Keep it instead of selecting an engine
                # whose pipeline is missing.
                return self.status()
        self._mode = normalized
        return self.status()

    def set_kokoro_voice(self, voice_name: str) -> None:
        self._kokoro_voice = voice_name.strip()

    def set_kokoro_speed(self, speed: float) -> None:
        self._kokoro_speed = max(0.5, min(2.0, speed))

    def set_output_index(self, index: int | None) -> dict[str, Any]:
        if index is None:
            self._preferred_output_index = None
            self._audio_outputs_cache = None
            return self.status()
        audio_info = self._audio_outputs(force=True)
        count = int(audio_info["count"])
        if index < 0 or index >= count:
            raise ValueError(f"Invalid output index {index}.")
        self._preferred_output_index = index
        self._audio_outputs_cache = None
        return self.status()

    def _audio_outputs(self, force: bool = False) -> dict[str, Any]:
        """Return cached audio-output info; never block the caller on COM.

        ``force=True`` enumerates synchronously and must only be used from
        threads that may legitimately block (TTS worker, explicit settings
        actions). Otherwise a stale cache is served and a daemon thread
        refreshes it in the background.
        """
        now = time.monotonic()
        cache = self._audio_outputs_cache
        if not force and cache is not None and (now - self._audio_outputs_ts) < _AUDIO_OUTPUTS_TTL_SECONDS:
            return cache
        if force:
            self._refresh_audio_outputs()
            return self._audio_outputs_cache or {"count": 0, "devices": []}
        self._schedule_audio_refresh()
        return cache or {"count": 0, "devices": []}

    def _schedule_audio_refresh(self) -> None:
        with self._audio_refresh_lock:
            now = time.monotonic()
            if self._audio_refresh_inflight or (now - self._audio_refresh_last_attempt) < 10.0:
                return
            self._audio_refresh_inflight = True
            self._audio_refresh_last_attempt = now

        def _worker() -> None:
            try:
                self._refresh_audio_outputs()
            finally:
                with self._audio_refresh_lock:
                    self._audio_refresh_inflight = False

        threading.Thread(target=_worker, daemon=True, name="jarvis-audio-outputs").start()

    def _refresh_audio_outputs(self) -> None:
        try:
            import win32com.client

            voice = win32com.client.Dispatch("SAPI.SpVoice")
            outputs = voice.GetAudioOutputs()
            devices = [{"index": i, "name": outputs.Item(i).GetDescription()} for i in range(outputs.Count)]
            result = {"count": int(outputs.Count), "devices": devices}
        except Exception:
            result = {"count": 0, "devices": []}
        self._audio_outputs_cache = result
        self._audio_outputs_ts = time.monotonic()

    def _speak_sapi_direct(self, text: str) -> bool:
        try:
            import win32com.client

            voice = win32com.client.Dispatch("SAPI.SpVoice")
            outputs = voice.GetAudioOutputs()
            if outputs.Count == 0:
                self._last_error = "no_audio_output_device_visible_to_runtime"
                return False
            if self._preferred_output_index is not None:
                if self._preferred_output_index < 0 or self._preferred_output_index >= outputs.Count:
                    self._last_error = f"preferred_output_out_of_range:{self._preferred_output_index}"
                    return False
                voice.AudioOutput = outputs.Item(self._preferred_output_index)
            voice.Speak(text)
            self._last_error = None
            self._engine_name = "windows_sapi_direct"
            return True
        except Exception as exc:
            self._last_error = f"sapi_direct_failed: {exc}"
            return False

    def _speak_windows_fallback(self, text: str) -> None:
        safe_text = text.replace("'", "''")
        command = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 0; "
            f"$s.Speak('{safe_text}')"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if completed.returncode != 0:
            self._last_error = f"sapi_fallback_failed: {completed.stderr.strip()}"
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass

    def _speak_kokoro(self, text: str) -> bool:
        if self._kokoro_pipeline is None:
            self._init_kokoro()
        if self._kokoro_pipeline is None:
            self._last_error = "kokoro_pipeline_not_initialized"
            return False
        try:
            import numpy as np  # type: ignore[import-untyped]
            import sounddevice as sd  # type: ignore[import-untyped]

            audio_chunks: list[np.ndarray] = []
            sample_rate = 24000
            for _graphemes, _phonemes, audio in self._kokoro_pipeline(
                text,
                voice=self._kokoro_voice,
                speed=self._kokoro_speed,
            ):
                if audio is None:
                    continue
                arr = np.asarray(audio)
                audio_chunks.append(arr)
            if not audio_chunks:
                self._last_error = "kokoro_empty_audio"
                return False
            full_audio = np.concatenate(audio_chunks)
            sd.play(full_audio, samplerate=sample_rate)
            sd.wait()
            self._last_error = None
            self._engine_name = "kokoro"
            return True
        except Exception as exc:
            self._last_error = f"kokoro_speak_failed: {exc}"
            return False

    def speak(self, text: str) -> None:
        if not self.enabled or not text:
            return
        with self._lock:
            if self._mode == "kokoro":
                if self._speak_kokoro(text):
                    return
            # TTS runs on worker threads, so a synchronous enumeration is safe here.
            audio_info = self._audio_outputs(force=True)
            if audio_info["count"] == 0:
                self._last_error = "no_audio_output_device_visible_to_runtime"
                try:
                    import winsound

                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
                return
            if self._mode == "sapi":
                if self._speak_sapi_direct(text):
                    return
            engine = self._ensure_pyttsx3_engine()
            if engine is not None:
                try:
                    if self._mode != "pyttsx3" and self._preferred_output_index is not None:
                        # If routing is explicit, prefer sapi direct over pyttsx3.
                        if self._speak_sapi_direct(text):
                            return
                    engine.say(text)
                    engine.runAndWait()
                    self._last_error = None
                    self._engine_name = "pyttsx3"
                    return
                except Exception as exc:
                    self._last_error = f"pyttsx3_speak_failed: {exc}"
            self._speak_windows_fallback(text)
