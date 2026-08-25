"""Voice-safety fixes: prompt echo, followup garbage gate, wake slicing, open targets."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import cast

import pytest

from app.config import Settings
from app.core.nlu import build_stt_prompt, has_lexicon_support, strip_prompt_echo
from app.core.orchestrator import JarvisOrchestrator
from app.core.router import DirectCommandRouter
from app.tools.registry import ToolRegistry

# ------------------------------------------------------------- STT prompt echo


def test_build_stt_prompt_contains_no_instructions() -> None:
    prompt = build_stt_prompt("jarvis")
    assert "ignore filler words" not in prompt.lower()
    assert "jarvis" in prompt and "youtube" in prompt  # vocabulary bias preserved


def test_strip_prompt_echo_removes_scaffolding_only() -> None:
    full_prompt = build_stt_prompt("jarvis")
    assert strip_prompt_echo(full_prompt) == ""

    assert strip_prompt_echo("Ignore filler words") == ""
    mixed = "open whatsapp ignore filler words"
    assert strip_prompt_echo(mixed) == "open whatsapp"

    assert strip_prompt_echo("play lofi on youtube") == "play lofi on youtube"
    assert strip_prompt_echo("open chrome") == "open chrome"


# ------------------------------------------------------------- garbage gate


def test_lexicon_support_gate() -> None:
    assert not has_lexicon_support("ignore filler words")
    assert not has_lexicon_support("heart shot")
    assert has_lexicon_support("open whatsapp")
    assert has_lexicon_support("what time is it")
    assert has_lexicon_support("skip 30")
    assert has_lexicon_support("search web hot tub restaurants")


def _voice_controller():
    from app.voice.loop import VoiceController

    settings = SimpleNamespace(
        groq_model="test-model",
        wake_word="jarvis",
        groq_api_key="",
        openai_api_key="",
        tts_enabled=False,
        tts_engine="sapi",
        kokoro_voice="af_bella",
        kokoro_lang_code="a",
        kokoro_speed=1.0,
        voice_min_transcript_chars=2,
        voice_followup_sticky_mode=True,
        voice_followup_window_seconds=10,
        voice_followup_max_seconds=120,
        voice_confirm_before_send=False,
        max_voice_response_chars=260,
        voice_watchdog_enabled=False,
    )
    orchestrator = cast(JarvisOrchestrator, SimpleNamespace(registry=None, memory=None))
    return VoiceController(cast(Settings, settings), orchestrator)


def test_fuzzy_wake_extraction_preserves_command() -> None:
    controller = _voice_controller()

    command, used_wake = controller._extract_command("jervis open whatsapp")
    assert used_wake is True
    assert command == "open whatsapp", "fuzzy wake match must never slice at -1"

    assert controller._extract_command("jarvis open whatsapp") == ("open whatsapp", True)

    command, used_wake = controller._extract_command("hey jervis could you pause youtube")
    assert used_wake is True
    assert "jervis" not in command.lower()
    assert command == "hey could you pause youtube", command


def test_sticky_followup_expires() -> None:
    controller = _voice_controller()
    controller._sticky_followup_active = True
    controller._sticky_started_at = time.monotonic() - 999999

    result = controller._extract_command("open whatsapp")
    assert result == ("", False), "expired sticky mode must require the wake word"


def test_non_command_followup_input_is_dropped_not_planned() -> None:
    from app.voice.listener import ListenResult

    controller = _voice_controller()
    controller._run_loop = lambda: time.sleep(5)  # type: ignore[method-assign]
    try:
        controller.start()
        controller._sticky_followup_active = True
        controller._sticky_started_at = time.monotonic()
        controller.listener.listen_once = lambda stop_event=None: ListenResult(  # type: ignore[method-assign]
            transcript="Ignore filler words", issue=None
        )

        controller._run_loop_iteration()

        assert controller._last_command == ""  # never executed
        assert controller._unknown_audio_count == 1
        assert str(controller.status()["last_error"]) == "non_command_transcript"
    finally:
        controller.stop(timeout=2.0)


def test_legitimate_followup_commands_still_execute() -> None:
    from app.voice.listener import ListenResult

    class _RecordingOrchestrator:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def execute_command(self, command: str, source: str, auto_approve: bool = False):
            self.commands.append(command)

            class _Resp:
                status = "completed"
                response_text = "done"

            return _Resp()

    orchestrator = _RecordingOrchestrator()

    from app.voice.loop import VoiceController

    settings = SimpleNamespace(
        groq_model="test-model",
        wake_word="jarvis",
        groq_api_key="",
        openai_api_key="",
        tts_enabled=False,
        tts_engine="sapi",
        kokoro_voice="af_bella",
        kokoro_lang_code="a",
        kokoro_speed=1.0,
        voice_min_transcript_chars=2,
        voice_followup_sticky_mode=True,
        voice_followup_window_seconds=10,
        voice_followup_max_seconds=120,
        voice_confirm_before_send=False,
        max_voice_response_chars=260,
        voice_watchdog_enabled=False,
    )
    controller = VoiceController(
        cast(Settings, settings),
        cast(JarvisOrchestrator, orchestrator),
    )
    controller._run_loop = lambda: time.sleep(5)  # type: ignore[method-assign]
    try:
        controller.start()
        controller._sticky_followup_active = True
        controller._sticky_started_at = time.monotonic()
        controller.listener.listen_once = lambda stop_event=None: ListenResult(  # type: ignore[method-assign]
            transcript="youtube next", issue=None
        )

        controller._run_loop_iteration()

        assert orchestrator.commands == ["youtube next"]
    finally:
        controller.stop(timeout=2.0)


# ------------------------------------------------------------- open-target safety


def test_router_open_targets() -> None:
    router = DirectCommandRouter()

    resolved = router.route("open whatsapp")
    assert resolved and resolved[0].tool == "open_url"

    chrome = router.route("open chrome")
    assert chrome and chrome[0].tool == "open_application" and chrome[0].args["name"] == "chrome"

    assert router.route("open heartshot") == [], "unknown target must fall through, not execute"
    assert router.route("open heart shot") == []
    assert router.route("launch totally unknown thing") == []


def test_unknown_app_fails_safely_without_launching(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tools.system_tools as st

    registry = ToolRegistry()
    st.register_system_tools(registry, cast(Settings, SimpleNamespace()))
    spec = registry.get("open_application")
    assert spec is not None

    raised = False
    try:
        spec.handler({"name": "heartshot"})
    except FileNotFoundError:
        raised = True
    assert raised, "unknown ASR-garbage target must fail safely, never shell-execute"

    opened_urls: list[str] = []
    monkeypatch.setattr(st.webbrowser, "open", lambda url, new=0: opened_urls.append(url) or True)

    opened = spec.handler({"name": "youtube"})
    assert opened["kind"] == "url"
    assert opened_urls == ["https://www.youtube.com"]
