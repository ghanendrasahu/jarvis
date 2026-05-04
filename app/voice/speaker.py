from __future__ import annotations

import os
import subprocess
from threading import Lock
from typing import Any


class Speaker:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._lock = Lock()
        self._engine = None
        self._engine_name = "disabled"
        self._last_error: str | None = None
        self._mode: str = "sapi" if os.name == "nt" else "pyttsx3"
        self._preferred_output_index: int | None = None
        if enabled:
            try:
                import pyttsx3

                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", 190)
                self._engine_name = "pyttsx3"
            except Exception as exc:
                self._last_error = f"pyttsx3_init_failed: {exc}"
                self._engine = None
                self._engine_name = "windows_sapi_fallback"
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
        }

    def set_mode(self, mode: str) -> dict[str, Any]:
        normalized = mode.strip().lower()
        if normalized not in {"pyttsx3", "sapi"}:
            raise ValueError("mode must be one of: pyttsx3, sapi")
        self._mode = normalized
        return self.status()

    def set_output_index(self, index: int | None) -> dict[str, Any]:
        if index is None:
            self._preferred_output_index = None
            return self.status()
        audio_info = self._audio_outputs()
        count = int(audio_info["count"])
        if index < 0 or index >= count:
            raise ValueError(f"Invalid output index {index}.")
        self._preferred_output_index = index
        return self.status()

    def _audio_outputs(self) -> dict[str, Any]:
        try:
            import win32com.client

            voice = win32com.client.Dispatch("SAPI.SpVoice")
            outputs = voice.GetAudioOutputs()
            devices = [{"index": i, "name": outputs.Item(i).GetDescription()} for i in range(outputs.Count)]
            return {"count": int(outputs.Count), "devices": devices}
        except Exception:
            return {"count": 0, "devices": []}

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
        )
        if completed.returncode != 0:
            self._last_error = f"sapi_fallback_failed: {completed.stderr.strip()}"
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass

    def speak(self, text: str) -> None:
        if not self.enabled or not text:
            return
        with self._lock:
            audio_info = self._audio_outputs()
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
            if self._engine is not None:
                try:
                    if self._mode != "pyttsx3" and self._preferred_output_index is not None:
                        # If routing is explicit, prefer sapi direct over pyttsx3.
                        if self._speak_sapi_direct(text):
                            return
                    self._engine.say(text)
                    self._engine.runAndWait()
                    self._last_error = None
                    self._engine_name = "pyttsx3"
                    return
                except Exception as exc:
                    self._last_error = f"pyttsx3_speak_failed: {exc}"
            self._speak_windows_fallback(text)
