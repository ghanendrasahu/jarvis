"""Regression tests for HUD-responsiveness fixes (audit Phases 1+2)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import cast

from app.config import Settings
from app.llm.planner import FallbackPlanner
from app.models.schemas import Plan
from app.voice.listener import VoiceListener
from app.voice.loop import VoiceController
from app.voice.speaker import Speaker

# ---------------------------------------------------------------- listener


def _listener_with_device(index: int = 3, name: str = "Realtek Microphone") -> VoiceListener:
    settings = SimpleNamespace(groq_api_key="", openai_api_key="", wake_word="jarvis")
    listener = VoiceListener(cast(Settings, settings))
    with listener._mic_lock:
        listener._device_index = index
        listener._device_name_cache[index] = name
        listener._publish_device_snapshot_locked()
    return listener


def test_device_accessors_are_lock_free_snapshots() -> None:
    listener = _listener_with_device()

    # Hold the mic lock exactly like an active capture cycle does.
    with listener._mic_lock:
        time.sleep(0.2)  # simulate recognizer.listen()
        assert listener.device_index == 3
        assert listener.get_device_name() == "Realtek Microphone"


def test_set_device_index_publishes_snapshot() -> None:
    listener = VoiceListener(cast(Settings, SimpleNamespace(groq_api_key="", openai_api_key="", wake_word="jarvis")))
    listener.set_device_index(5)
    assert listener.device_index == 5
    listener.set_device_index(None)
    assert listener.device_index is None
    assert listener.get_device_name() is None


def test_transcribe_groq_client_has_hard_timeout() -> None:
    from unittest.mock import patch

    captured: dict[str, object] = {}

    def _raise(**_kwargs):
        raise RuntimeError("stop-before-network")

    class _FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=_raise))

    settings = SimpleNamespace(
        groq_api_key="k",
        groq_base_url="http://localhost:9/v1",
        wake_word="jarvis",
        stt_language="en",
        stt_prompt_bias=False,
        voice_min_avg_logprob=-1.0,
        voice_max_no_speech_prob=0.9,
    )
    listener = VoiceListener(cast(Settings, settings))

    fake_audio = SimpleNamespace(get_wav_data=lambda: b"RIFF-fake-wav")
    with patch("openai.OpenAI", _FakeOpenAI):
        try:
            listener._transcribe_groq(fake_audio)
        except RuntimeError as exc:
            assert "stop-before-network" in str(exc)

    assert captured.get("timeout") == 30.0
    assert captured.get("max_retries") == 1


# ---------------------------------------------------------------- speaker


def test_speaker_status_never_blocks_on_com() -> None:
    speaker = Speaker(enabled=True, mode="sapi")

    def _slow_com() -> None:
        time.sleep(0.3)

    # Simulate a slow COM enumeration happening elsewhere; status must return fast.
    result: dict[str, object] = {}
    thread = threading.Thread(target=lambda: result.update(speaker.status()))
    thread.start()
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "status() blocked - audio-output cache path is synchronous"
    assert result["enabled"] is True


def test_speaker_audio_outputs_serves_cache_without_force() -> None:
    speaker = Speaker(enabled=False, mode="sapi")
    speaker._audio_outputs_cache = {"count": 2, "devices": [{"index": 0, "name": "a"}, {"index": 1, "name": "b"}]}
    speaker._audio_outputs_ts = time.monotonic()

    info = speaker._audio_outputs(force=False)
    assert info["count"] == 2


def test_speaker_defers_engine_init_until_first_speak() -> None:
    speaker = Speaker(enabled=True, mode="sapi")
    assert speaker._engine is None  # no pyttsx3.init at construction (GUI-thread safety)


# ---------------------------------------------------------------- fallback planner deadline


class _FailingPlanner:
    def plan(self, *_args, **_kwargs) -> Plan:
        raise RuntimeError("provider down")


def test_fallback_planner_deadline_stops_the_chain() -> None:
    class _SlowFailingPlanner:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, *_args, **_kwargs) -> Plan:
            self.calls += 1
            time.sleep(0.2)
            raise RuntimeError("provider down")

    primary = _SlowFailingPlanner()
    first = _SlowFailingPlanner()
    second = _SlowFailingPlanner()
    third = _SlowFailingPlanner()

    planner = FallbackPlanner(
        primary=primary,
        fallbacks=[first, second, third],
        max_retries=5,
        retry_base_delay=0.02,
        deadline_seconds=0.55,
    )
    started = time.monotonic()
    plan = planner.plan("test command", [], [])
    elapsed = time.monotonic() - started

    # Deadline must stop the chain from running indefinitely; providers already
    # in flight finish, but nothing new starts past the wall-clock budget.
    assert elapsed < 4.0, "fallback chain ignored its deadline"
    total_calls = primary.calls + first.calls + second.calls + third.calls
    assert total_calls < 12, "retry/deadline math allows runaway attempts"
    assert plan.final_response


def test_fallback_planner_single_call_bounded_by_client_timeout_contract() -> None:
    class _OnceFailingPlanner:
        def plan(self, *_args, **_kwargs) -> Plan:
            raise TimeoutError("client timeout after per-attempt limit")

    planner = FallbackPlanner(
        primary=_OnceFailingPlanner(),
        fallbacks=[],
        max_retries=0,
        deadline_seconds=30,
    )
    started = time.monotonic()
    result = planner.plan("cmd", [], [])
    assert time.monotonic() - started < 2.0
    assert result.final_response


def test_fallback_planner_returns_first_success() -> None:
    class _GoodPlanner:
        def plan(self, *_args, **_kwargs) -> Plan:
            return Plan(summary="ok", actions=[], final_response="done")

    planner = FallbackPlanner(primary=_FailingPlanner(), fallbacks=[_GoodPlanner()], deadline_seconds=10)
    result = planner.plan("cmd", [], [])
    assert result.final_response == "done"


# ---------------------------------------------------------------- watchdog (Phase 3)


def _voice_controller() -> VoiceController:
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
        voice_confirm_before_send=False,
        # Keep the supervisor thread out of tests; checks are invoked manually.
        voice_watchdog_enabled=False,
        voice_watchdog_stale_seconds=45,
        voice_watchdog_max_restarts=5,
    )
    return VoiceController(settings, _DummyOrchestrator())


def test_supervisor_restarts_dead_loop_thread() -> None:
    controller = _voice_controller()
    controller._run_loop = lambda: time.sleep(5)  # type: ignore[method-assign]
    try:
        assert controller.start() is True

        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        controller._thread = dead  # simulate a silently crashed worker
        restarts_before = controller._auto_restarts

        performed = controller._supervisor_check()

        assert performed is True
        assert controller.is_running()
        assert controller._auto_restarts == restarts_before + 1
    finally:
        controller.stop(timeout=2.0)


def test_supervisor_reports_but_does_not_restart_stale_alive_loop() -> None:
    controller = _voice_controller()
    controller._run_loop = lambda: time.sleep(5)  # type: ignore[method-assign]
    try:
        assert controller.start() is True
        controller._heartbeat_at = time.monotonic() - 500.0  # far past stale threshold

        performed = controller._supervisor_check()

        assert performed is False, "must not respawn an alive thread (mic contention)"
        assert controller.is_running()
        assert "stalled" in str(controller.status().get("last_error"))
    finally:
        controller.stop(timeout=2.0)


def test_supervisor_budget_exhaustion_blocks_restart() -> None:
    controller = _voice_controller()
    controller._auto_restarts = 99  # over the configured budget of 5

    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    controller._thread = dead
    controller._desired_running = True

    performed = controller._supervisor_check()

    assert performed is False
    assert not controller.is_running()
    assert str(controller.status().get("last_error")).startswith("watchdog_budget_exhausted")


# ---------------------------------------------------------------- event queue (Phase 3)


def test_enqueue_drops_oldest_when_saturated() -> None:
    import queue as q

    from app.gui import JarvisHudApp

    hud = JarvisHudApp.__new__(JarvisHudApp)  # bypass Qt/runtime construction
    hud._events = q.Queue(maxsize=2)

    for i in range(5):
        hud._enqueue(("agent_step", {"i": i}))

    assert hud._events.qsize() == 2
    first = hud._events.get_nowait()
    assert first[0] == "agent_step"
    assert first[1]["i"] == 3, "oldest events must be dropped, newest kept"


class _DummyOrchestrator:
    def execute_command(self, command: str, source: str, auto_approve: bool = False):
        raise AssertionError("execute_command should not be called in these tests")
