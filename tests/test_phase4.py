"""Phase 4 hardening tests: orphan-worker cap, CI coverage, failure backoff."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.executor import ActionExecutor, orphaned_workers_snapshot
from app.core.orchestrator import JarvisOrchestrator
from app.models.schemas import Action
from app.tools.registry import ToolRegistry
from app.voice.listener import ListenResult
from app.voice.loop import VoiceController

# ---------------------------------------------------------------- executor orphans


def _executor_with_sleep_tool(sleep_seconds: float, timeout_seconds: float, max_orphans: int) -> ActionExecutor:
    def slow_handler(_args: dict) -> dict:
        time.sleep(sleep_seconds)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        name="slow_tool",
        description="sleeps longer than its timeout",
        risk="low",
        parameters={},
        handler=slow_handler,
        category="system",
        read_write="read",
        timeout_seconds=timeout_seconds,
    )
    settings = SimpleNamespace(
        approval_policy="none",
        auto_approve_low_risk=True,
        tool_retry_attempts=0,
        tool_retry_backoff_seconds=0.0,
        executor_max_orphaned_workers=max_orphans,
    )
    approvals = SimpleNamespace()
    executor = ActionExecutor(
        registry=registry,
        approvals=cast(ApprovalManager, approvals),
        settings=cast(Settings, settings),
    )
    return executor


def _slow_action() -> Action:
    return Action(tool="slow_tool", args={}, risk="low", reason="test")


def test_timed_out_tool_becomes_tracked_orphan_then_drains() -> None:
    executor = _executor_with_sleep_tool(sleep_seconds=0.9, timeout_seconds=0.15, max_orphans=16)

    result = executor.execute("cmd", _slow_action(), auto_approve=True)

    assert result.status == "failed"
    assert "timed out" in str(result.error)
    assert orphaned_workers_snapshot()["count"] == 1
    assert orphaned_workers_snapshot()["tools"] == ["slow_tool"]

    time.sleep(1.0)  # let the abandoned worker finish cooperatively
    assert orphaned_workers_snapshot()["count"] == 0


def test_orphan_cap_fails_fast_without_spawning() -> None:
    executor = _executor_with_sleep_tool(sleep_seconds=0.9, timeout_seconds=0.15, max_orphans=1)

    first = executor.execute("cmd", _slow_action(), auto_approve=True)
    assert first.status == "failed"  # now one orphan is alive

    second = executor.execute("cmd", _slow_action(), auto_approve=True)
    assert second.status == "failed"
    assert "orphaned tool workers at limit (1/1)" in str(second.error)

    time.sleep(1.0)  # cap releases once the orphan drains
    third = executor.execute("cmd", _slow_action(), auto_approve=True)
    assert third.status == "failed"
    assert "at limit" not in str(third.error)


# ---------------------------------------------------------------- CI protection


def test_ci_workflow_runs_lint_typecheck_and_tests() -> None:
    workflow_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    content = workflow_path.read_text(encoding="utf-8")

    assert "ruff check app tests" in content
    assert "mypy app" in content
    assert "pytest tests -q" in content
    assert "windows-latest" in content  # suite touches Windows-only modules


# ---------------------------------------------------------------- voice backoff


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
        voice_watchdog_enabled=False,
    )

    class _Dummy:
        def execute_command(self, command: str, source: str, auto_approve: bool = False):
            raise AssertionError

    return VoiceController(
        cast(Settings, settings),
        cast(JarvisOrchestrator, _Dummy()),
    )


def test_persistent_failures_back_off_and_recover() -> None:
    controller = _voice_controller()

    sleeps: list[float] = []
    original_sleep = time.sleep

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    failures = iter(
        [ListenResult(transcript=None, issue="microphone_init_failed") for _ in range(4)]
    )
    controller.listener.listen_once = lambda stop_event=None: next(failures)  # type: ignore[method-assign]

    time.sleep = fake_sleep  # type: ignore[assignment]
    try:
        for _ in range(4):
            controller._run_loop_iteration()
    finally:
        time.sleep = original_sleep  # type: ignore[assignment]

    assert sleeps == [0.25, 0.5, 0.75, 1.0], "backoff must grow and stay bounded"


def test_idle_results_do_not_trigger_backoff() -> None:
    controller = _voice_controller()
    results = iter([ListenResult(transcript=None, issue="timeout") for _ in range(3)])
    controller.listener.listen_once = lambda stop_event=None: next(results)  # type: ignore[method-assign]

    for _ in range(3):
        controller._run_loop_iteration()

    assert controller._consecutive_failures == 0
