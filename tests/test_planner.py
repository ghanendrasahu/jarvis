"""B5 regression tests: FallbackPlanner retry parity across all providers."""

from __future__ import annotations

from typing import Any

import pytest

import app.llm.planner as planner_module
from app.llm.planner import ALL_PROVIDERS_FAILED_SUMMARY, FallbackPlanner
from app.models.schemas import Action, Plan


class FakeClock:
    """Deterministic stand-in for the stdlib time module inside planner."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeProviderBase:
    """Scripted provider: pops one entry per call (Exception -> raise, else Plan)."""

    calls = 0
    _behaviors: list[Any] = []
    display_name = "Fake"

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        self.calls += 1
        if not self._behaviors:
            raise AssertionError(f"{self.display_name}: unexpected extra plan() call")
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


def fake_provider(name: str, behaviors: list[Any]) -> Any:
    """Build a scripted provider whose type name feeds FallbackPlanner's logs."""

    class _Scripted(_FakeProviderBase):
        pass

    _Scripted.__name__ = name
    _Scripted.__qualname__ = name
    instance = _Scripted()
    instance.display_name = name
    instance.calls = 0
    instance._behaviors = list(behaviors)
    return instance


TIMEOUT_ERR = RuntimeError("503 service unavailable: request timed out")
PAYLOAD_ERR = RuntimeError("413 payload too large")
BAD_REQUEST_ERR = RuntimeError("400 bad request")


def ok_plan(tag: str = "ok") -> Plan:
    return Plan(
        summary=f"plan-{tag}",
        actions=[Action(tool="get_current_time", args={}, risk="low")],
        final_response=f"response-{tag}",
    )


def make_chain(
    primary: Any,
    fallbacks: list[Any],
    max_retries: int,
    delay: float = 0.001,
    deadline: float = 60.0,
) -> FallbackPlanner:
    return FallbackPlanner(
        primary,
        list(fallbacks),
        max_retries=max_retries,
        retry_base_delay=delay,
        deadline_seconds=deadline,
    )


# --------------------------------------------------------------------------- retries


def test_primary_single_attempt_when_retries_disabled() -> None:
    primary = fake_provider("PrimaryA", [TIMEOUT_ERR])
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=0)

    result = chain.plan("do it", [], [])

    assert primary.calls == 1, "max_retries=0 must give the primary exactly one attempt"
    assert fallback.calls == 1
    assert result.final_response == "response-fb"


def test_primary_retried_once_with_max_retries_one() -> None:
    primary = fake_provider("PrimaryA", [TIMEOUT_ERR, TIMEOUT_ERR])
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=1)

    chain.plan("do it", [], [])

    assert primary.calls == 2, "max_retries=1 must give the primary two attempts"


def test_primary_retried_twice_with_max_retries_two() -> None:
    primary = fake_provider("PrimaryA", [TIMEOUT_ERR] * 3)
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=2)

    chain.plan("do it", [], [])

    assert primary.calls == 3, "max_retries=2 must give the primary three attempts"


def test_fallback_provider_follows_same_retry_semantics() -> None:
    primary = fake_provider("PrimaryA", [PAYLOAD_ERR])  # switch immediately
    flaky = fake_provider("FlakyMid", [TIMEOUT_ERR, TIMEOUT_ERR, TIMEOUT_ERR])
    last = fake_provider("LastResort", [ok_plan("last")])
    chain = make_chain(primary, [flaky, last], max_retries=2)

    result = chain.plan("do it", [], [])

    assert primary.calls == 1
    assert flaky.calls == 3, "fallbacks must receive max_retries + 1 attempts too"
    assert last.calls == 1
    assert result.final_response == "response-last"


@pytest.mark.parametrize("err", [PAYLOAD_ERR, BAD_REQUEST_ERR])
def test_non_retryable_primary_failure_is_not_retried(err: Exception) -> None:
    primary = fake_provider("PrimaryA", [err])
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=2)

    chain.plan("do it", [], [])

    assert primary.calls == 1, "provider-switch errors must never be retried"
    assert fallback.calls == 1


def test_retry_exhaustion_advances_to_next_provider() -> None:
    primary = fake_provider("PrimaryA", [TIMEOUT_ERR, TIMEOUT_ERR])
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=1)

    result = chain.plan("do it", [], [])

    assert primary.calls == 2
    assert fallback.calls == 1
    assert result.summary == "plan-fb"
    assert chain.last_provider == "FallbackB"


def test_deadline_bounds_attempts_without_unbounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(planner_module, "time", clock)
    monkeypatch.setattr(planner_module.random, "uniform", lambda a, b: 0.0)  # disable jitter

    primary = fake_provider("PrimaryA", [TIMEOUT_ERR] * 6)
    fallback = fake_provider("FallbackB", [TIMEOUT_ERR] * 6)
    chain = make_chain(primary, [fallback], max_retries=5, delay=4.0, deadline=10.0)

    result = chain.plan("do it", [], [])

    assert primary.calls == 2, "retry delays must consume the shared wall-clock budget"
    assert fallback.calls == 2
    assert clock.monotonic() <= 10.0, "attempts must never push past the hard deadline"
    assert result.summary == ALL_PROVIDERS_FAILED_SUMMARY


def test_successful_retry_returns_exactly_one_success() -> None:
    good = ok_plan("good")
    primary = fake_provider("PrimaryA", [TIMEOUT_ERR, good])
    fallback = fake_provider("FallbackB", [])
    chain = make_chain(primary, [fallback], max_retries=2)

    result = chain.plan("do it", [], [])

    assert result is good
    assert primary.calls == 2
    assert fallback.calls == 0
    assert chain.last_provider == "PrimaryA"
    assert chain.last_error == ""


def test_existing_fallback_ordering_remains_intact() -> None:
    primary = fake_provider("PrimaryA", [ok_plan("primary")])
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=2)

    result = chain.plan("do it", [], [])

    assert result.summary == "plan-primary"
    assert fallback.calls == 0, "healthy primary must short-circuit the chain"


# ---------------------------------------------------------------------------
# P1-5: Cooldown and jitter tests
# ---------------------------------------------------------------------------


def test_cooldown_skips_failing_provider_on_subsequent_calls() -> None:
    """After a provider exhausts retries, it enters cooldown and is skipped next call."""
    clock = FakeClock()
    primary = fake_provider("PrimaryA", [
        RuntimeError("500 internal error"),
        RuntimeError("500 internal error"),
    ])
    # Fallback needs behaviors for both calls
    fallback = fake_provider("FallbackB", [ok_plan("fb"), ok_plan("fb2")])
    chain = make_chain(primary, [fallback], max_retries=1)
    import app.llm.planner as mod
    orig_monotonic = mod.time.monotonic
    orig_sleep = mod.time.sleep
    orig_uniform = mod.random.uniform
    mod.time.monotonic = clock.monotonic
    mod.time.sleep = clock.sleep
    mod.random.uniform = lambda a, b: 0.0  # disable jitter
    try:
        # First call: primary exhausts, fallback succeeds
        result = chain.plan("cmd", [], [])
        assert result.summary == "plan-fb"
        assert primary.calls == 2  # 1 retry + 1 initial

        # Second call: primary should be in cooldown, fallback should be used directly
        primary.calls = 0
        result2 = chain.plan("cmd2", [], [])
        assert result2.summary == "plan-fb2"
        assert primary.calls == 0, "primary should be skipped due to cooldown"
        assert fallback.calls == 2
    finally:
        mod.time.monotonic = orig_monotonic
        mod.time.sleep = orig_sleep
        mod.random.uniform = orig_uniform


def test_cooldown_expires_after_sufficient_time() -> None:
    """After cooldown expires, the provider is tried again."""
    clock = FakeClock()
    primary = fake_provider("PrimaryA", [
        RuntimeError("500 error"),
        RuntimeError("500 error"),  # exhausted -> cooldown
        ok_plan("recovered"),  # should be reached after cooldown expires
    ])
    chain = make_chain(primary, [], max_retries=1)
    import app.llm.planner as mod
    orig_monotonic = mod.time.monotonic
    orig_sleep = mod.time.sleep
    orig_uniform = mod.random.uniform
    mod.time.monotonic = clock.monotonic
    mod.time.sleep = clock.sleep
    mod.random.uniform = lambda a, b: 0.0  # disable jitter
    try:
        # First call: primary exhausts
        result = chain.plan("cmd", [], [])
        assert result.summary == ALL_PROVIDERS_FAILED_SUMMARY

        # Advance time past cooldown (max cooldown is 300s, but with 1 retry
        # and base delay 1.0, cooldown = min(300, 1.0 * 2^2) = 4s)
        clock.now = 10.0  # well past any cooldown
        primary.calls = 0
        result2 = chain.plan("cmd2", [], [])
        assert result2.summary == "plan-recovered"
        assert primary.calls == 1, "primary should be tried again after cooldown expires"
    finally:
        mod.time.monotonic = orig_monotonic
        mod.time.sleep = orig_sleep
        mod.random.uniform = orig_uniform


def test_non_retryable_error_sets_cooldown() -> None:
    """Non-retryable errors (400/413) also set cooldown on the provider."""
    clock = FakeClock()
    primary = fake_provider("PrimaryA", [
        RuntimeError("400 bad request"),  # non-retryable -> cooldown
    ])
    fallback = fake_provider("FallbackB", [ok_plan("fb")])
    chain = make_chain(primary, [fallback], max_retries=2)
    import app.llm.planner as mod
    orig_monotonic = mod.time.monotonic
    orig_sleep = mod.time.sleep
    orig_uniform = mod.random.uniform
    mod.time.monotonic = clock.monotonic
    mod.time.sleep = clock.sleep
    mod.random.uniform = lambda a, b: 0.0  # disable jitter
    try:
        result = chain.plan("cmd", [], [])
        assert result.summary == "plan-fb"
        # Primary should now be in cooldown — advance clock to verify it stays skipped
        clock.now = 0.001  # just past the FakeClock start, still within cooldown
        primary.calls = 0
        fallback.calls = 0
        chain.plan("cmd2", [], [])
        assert primary.calls == 0, "primary should be skipped after non-retryable error"
        assert fallback.calls == 1
    finally:
        mod.time.monotonic = orig_monotonic
        mod.time.sleep = orig_sleep
        mod.random.uniform = orig_uniform
