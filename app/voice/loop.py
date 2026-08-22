from __future__ import annotations

import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.nlu import correct_transcript, needs_semantic_interpretation, strip_filler
from app.core.orchestrator import JarvisOrchestrator
from app.models.schemas import ExecuteCommandResponse
from app.voice.listener import VoiceListener
from app.voice.semantic import SemanticInterpreter
from app.voice.speaker import Speaker
from app.voice.wake_word_detector import (
    PhraseMatchingDetectorForIntegration,
)

logger = get_logger("voice")


class VoiceController:
    def __init__(self, settings: Settings, orchestrator: JarvisOrchestrator) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.listener = VoiceListener(settings)
        self.speaker = Speaker(
            enabled=settings.tts_enabled,
            mode=settings.tts_engine,
            kokoro_voice=settings.kokoro_voice,
            kokoro_lang_code=settings.kokoro_lang_code,
            kokoro_speed=settings.kokoro_speed,
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._started_at: str | None = None
        self._last_activity_at: str | None = None
        self._last_transcript: str = ""
        self._last_command: str = ""
        self._last_error: str | None = None
        self._listen_attempts: int = 0
        self._recognized_count: int = 0
        self._timeout_count: int = 0
        self._unknown_audio_count: int = 0
        self._request_error_count: int = 0
        self._followup_until: float = 0.0
        self._sticky_followup_active: bool = False
        self._tts_block_until: float = 0.0
        self._pending_whatsapp_confirmation: dict[str, str] | None = None
        self._last_normalization: dict[str, str] | None = None
        # Voice understanding pipeline knobs (getattr keeps duck-typed settings compatible).
        self._fuzzy_cutoff = int(getattr(settings, "voice_fuzzy_cutoff", 88))
        semantic_enabled = bool(getattr(settings, "voice_semantic_enabled", True))
        self._semantic = SemanticInterpreter(settings) if semantic_enabled else None
        self._tool_names: list[str] | None = None
        # Wake-word detector — fuzzy matching accepts spoken variants ("Jervis").
        self._wake_word = PhraseMatchingDetectorForIntegration(
            self.listener.settings.wake_word,
            fuzzy_enabled=bool(getattr(settings, "wake_word_fuzzy_enabled", True)),
            fuzzy_score=float(getattr(settings, "wake_word_fuzzy_score", 82.0)),
        )

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self.is_running():
                return False
            self._stop_event.clear()
            with self._state_lock:
                self._started_at = datetime.now(UTC).isoformat()
                self._last_activity_at = self._started_at
                self._last_transcript = ""
                self._last_command = ""
                self._last_error = None
                self._listen_attempts = 0
                self._recognized_count = 0
                self._timeout_count = 0
                self._unknown_audio_count = 0
                self._request_error_count = 0
                self._followup_until = 0.0
                self._sticky_followup_active = False
                self._tts_block_until = 0.0
                self._pending_whatsapp_confirmation = None
                self._last_normalization = None
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="jarvis-voice-loop")
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lifecycle_lock:
            if not self.is_running():
                return False
            self._stop_event.set()
            assert self._thread is not None
            thread = self._thread
            wait_seconds = 3
            thread.join(timeout=wait_seconds)
            if thread.is_alive():
                with self._state_lock:
                    self._last_error = "stop_timeout: listener still shutting down"
                return False
            self._thread = None
            return True

    def status(self) -> dict[str, Any]:
        # speaker.status() serves cached audio info (COM refresh happens on a
        # daemon thread), and listener device accessors are lock-free
        # snapshots - nothing here may block against an active mic capture.
        speaker_status = self.speaker.status()
        with self._state_lock:
            return {
                "running": self.is_running(),
                "wake_word": self.settings.wake_word,
                "stt_backend": self.listener.backend,
                "stt_language": str(getattr(self.settings, "stt_language", "") or ""),
                "semantic_normalizer": self._semantic.provider if self._semantic else "disabled",
                "mic_device_index": self.listener.device_index,
                "mic_device_name": self.listener.get_device_name(),
                "tts_enabled": self.speaker.enabled,
                "speaker": speaker_status,
                "started_at": self._started_at,
                "last_activity_at": self._last_activity_at,
                "last_transcript": self._last_transcript,
                "last_command": self._last_command,
                "last_error": self._last_error,
                "listen_attempts": self._listen_attempts,
                "recognized_count": self._recognized_count,
                "timeout_count": self._timeout_count,
                "unknown_audio_count": self._unknown_audio_count,
                "request_error_count": self._request_error_count,
                "followup_window_active": time.time() <= self._followup_until,
                "sticky_followup_active": self._sticky_followup_active,
                "tts_block_active": time.time() <= self._tts_block_until,
                "pending_whatsapp_confirmation": bool(self._pending_whatsapp_confirmation),
                "last_normalization": self._last_normalization,
            }

    def _speak_async(self, text: str) -> None:
        if not text or not self.speaker.enabled:
            return
        self._tts_block_until = time.time() + 2.5

        def _safe_speak() -> None:
            try:
                self.speaker.speak(text)
            except Exception:
                pass

        thread = threading.Thread(
            target=_safe_speak,
            daemon=True,
            name="jarvis-voice-tts",
        )
        thread.start()

    def _run_command(self, command: str) -> ExecuteCommandResponse:
        """Execute a command without ever letting an exception kill the voice loop."""
        try:
            return self.orchestrator.execute_command(command=command, source="voice", auto_approve=False)
        except Exception as exc:
            logger.exception("[voice] command failed: %s", command)
            return ExecuteCommandResponse(
                command=command,
                source="voice",
                status="failed",
                response_text=f"Command failed: {exc}",
                plan_summary="",
                results=[],
                created_at=datetime.now(UTC).isoformat(),
            )

    def _extract_command(self, transcript: str) -> tuple[str, bool]:
        # Use the partial phrase-matching detector as a convenience
        # during integration.  This is NOT a real hotword model; the
        # master prompt warns against claiming full wake-word support
        # without actual detection.
        used_wake_word = self._wake_word.detect_transcript(transcript)
        lowered = transcript.lower().strip()
        if not used_wake_word:
            if self._sticky_followup_active or time.time() <= self._followup_until:
                return transcript.strip(" ,."), False
            return "", False
        # Strip the wake word and surrounding whitespace/punctuation.
        index = lowered.find(self._wake_word.wake_phrase)
        command = transcript[index + len(self._wake_word.wake_phrase) :].strip(" ,.:")
        return command, True

    def _get_tool_names(self) -> list[str]:
        if self._tool_names is None:
            try:
                self._tool_names = [item["name"] for item in self.orchestrator.registry.catalog()]
            except Exception:
                self._tool_names = []
        return self._tool_names

    def _normalize_for_understanding(self, command: str) -> str:
        """Whisper output -> deterministic cleanup -> optional semantic LLM pass.

        Fast path: understood commands (or free-text queries) skip the LLM and go
        straight to the existing router. Suspicious transcripts get one semantic
        interpretation attempt; if it is not confident the original text flows on
        to the planner exactly as before.
        """
        cleaned = strip_filler(command)
        if not cleaned:
            return command
        corrected, _changed = correct_transcript(cleaned, cutoff=self._fuzzy_cutoff)
        if self._semantic is None or not needs_semantic_interpretation(corrected):
            return corrected

        learned_facts: dict[str, Any] = {}
        try:
            context = self.orchestrator.memory.get_state("runtime_context", default={}) or {}
            learned_facts = dict(context.get("learned_facts") or {})
        except Exception:
            logger.debug("runtime context unavailable for semantic pass", exc_info=True)
        interpreted = self._semantic.interpret(
            corrected,
            tool_names=self._get_tool_names(),
            learned_facts=learned_facts,
        )
        return interpreted or corrected

    def _should_ignore_transcript(self, transcript: str) -> bool:
        if time.time() <= self._tts_block_until:
            return True
        return not self.listener._is_transcript_plausible(transcript, self.settings.voice_min_transcript_chars)

    def _run_loop(self) -> None:
        model = self.settings.groq_model
        self._speak_async(f"Hello Boss Sahu, running on {model}. How can I help you?")
        while not self._stop_event.is_set():
            try:
                result = self.listener.listen_once(stop_event=self._stop_event)
            except Exception as exc:
                with self._state_lock:
                    self._last_activity_at = datetime.now(UTC).isoformat()
                    self._last_error = str(exc)
                time.sleep(0.5)
                continue

            with self._state_lock:
                self._listen_attempts += 1
                self._last_activity_at = datetime.now(UTC).isoformat()

            if result.issue:
                with self._state_lock:
                    if result.issue == "timeout":
                        self._timeout_count += 1
                    elif result.issue == "no_speech":
                        pass
                    elif result.issue == "garbled_transcript":
                        self._unknown_audio_count += 1
                    elif result.issue == "unknown_audio":
                        self._unknown_audio_count += 1
                    elif result.issue.startswith("request_error"):
                        self._request_error_count += 1
                        self._last_error = result.issue
                    else:
                        self._last_error = result.issue
                continue

            transcript = result.transcript or ""
            if not transcript:
                continue
            if self._should_ignore_transcript(transcript):
                continue

            with self._state_lock:
                self._last_transcript = transcript
                self._recognized_count += 1
                self._last_error = None

            lowered = transcript.lower().strip()
            if lowered.startswith("approve "):
                approval_id = lowered.split("approve ", 1)[1].strip()
                try:
                    approval_response = self.orchestrator.approve(approval_id)
                    message = approval_response.message
                except Exception as exc:
                    logger.exception("[voice] approval failed: %s", approval_id)
                    message = f"Approval failed: {exc}"
                logger.info("[voice] approval: %s", approval_id)
                logger.info("[voice] response: %s", message)
                self._speak_async(message[: self.settings.max_voice_response_chars])
                continue

            normalized = transcript.lower().strip()
            wake = self.settings.wake_word.lower().strip()
            if normalized in {f"{wake} sleep", "sleep", "go to sleep", "stop listening"}:
                self._sticky_followup_active = False
                self._followup_until = 0.0
                self._pending_whatsapp_confirmation = None
                self._speak_async("Follow-up mode off. Say jarvis to wake me again.")
                continue

            if normalized in {"cancel", "cancel that", "nevermind", "never mind", "stop"}:
                self._pending_whatsapp_confirmation = None
                self._followup_until = 0.0
                self._speak_async("Canceled.")
                continue

            if normalized in {"confirm send", "send now", "yes send it"} and self._pending_whatsapp_confirmation:
                pending = self._pending_whatsapp_confirmation
                command = f"send whatsapp to {pending['phone']}: {pending['message']}"
                execution = self._run_command(command)
                self._pending_whatsapp_confirmation = None
                logger.info("[voice] command: %s", command)
                logger.info("[voice] response: %s", execution.response_text)
                self._speak_async(execution.response_text[: self.settings.max_voice_response_chars])
                continue

            command, used_wake_word = self._extract_command(transcript)
            if not command:
                continue
            if used_wake_word:
                if self.settings.voice_followup_sticky_mode:
                    self._sticky_followup_active = True
                    self._followup_until = 0.0
                else:
                    self._followup_until = time.time() + max(0, int(self.settings.voice_followup_window_seconds))

            canonical_command = self._normalize_for_understanding(command)
            if canonical_command != command:
                with self._state_lock:
                    self._last_normalization = {"original": command, "canonical": canonical_command}
                logger.info("[voice] normalized: '%s' -> '%s'", command, canonical_command)
                command = canonical_command

            with self._state_lock:
                self._last_command = command

            # Safer voice UX: draft WhatsApp message first, then require explicit send confirmation.
            if self.settings.voice_confirm_before_send:
                send_match = re.match(
                    r"^(?:send\s+)?whatsapp(?:\s+message)?\s+to\s+(.+?)\s*[:\-]\s*(.+)$",
                    command.strip(),
                    flags=re.IGNORECASE,
                )
                if send_match:
                    phone = send_match.group(1).strip()
                    message = send_match.group(2).strip()
                    draft_command = f"open whatsapp chat {phone}: {message}"
                    execution = self.orchestrator.execute_command(command=draft_command, source="voice", auto_approve=False)
                    self._pending_whatsapp_confirmation = {"phone": phone, "message": message}
                    response = (
                        f"{execution.response_text} Draft is ready. Say 'confirm send' to send now or 'cancel'."
                    )
                    logger.info("[voice] command: %s", draft_command)
                    logger.info("[voice] response: %s", response)
                    self._speak_async(response[: self.settings.max_voice_response_chars])
                    continue

            execution = self._run_command(command)
            logger.info("[voice] command: %s", command)
            logger.info("[voice] response: %s", execution.response_text)
            self._speak_async(execution.response_text[: self.settings.max_voice_response_chars])

    def probe_once(self) -> dict[str, str | None]:
        result = self.listener.listen_once()
        return {
            "stt_backend": self.listener.backend,
            "issue": result.issue,
            "transcript": result.transcript,
        }

    def list_input_devices(self) -> dict[str, Any]:
        try:
            names = self.listener.list_microphones()
            devices = [{"index": idx, "name": name} for idx, name in enumerate(names)]
            return {"count": len(names), "devices": devices}
        except Exception as exc:
            return {"count": 0, "devices": [], "error": str(exc)}

    def set_input_device(self, index: int | None) -> dict[str, Any]:
        if index is None:
            self.listener.set_device_index(None)
            return {"selected_index": None, "selected_name": "default"}

        names = self.listener.list_microphones()
        if index < 0 or index >= len(names):
            raise ValueError(f"Invalid microphone index {index}. Use /voice devices to list valid indexes.")
        self.listener.set_device_index(index)
        return {"selected_index": index, "selected_name": names[index]}

    def list_output_devices(self) -> dict[str, Any]:
        status = self.speaker.status()
        return {
            "count": status.get("audio_output_count", 0),
            "devices": status.get("audio_outputs", []),
            "selected_index": status.get("preferred_output_index"),
            "mode": status.get("mode"),
        }

    def set_output_device(self, index: int | None) -> dict[str, Any]:
        return self.speaker.set_output_index(index)

    def set_speaker_mode(self, mode: str) -> dict[str, Any]:
        return self.speaker.set_mode(mode)
