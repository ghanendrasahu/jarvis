from __future__ import annotations

import threading
import time
from typing import cast

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.models.schemas import Action, RiskLevel, ToolExecutionResult
from app.tools.registry import ToolRegistry

RISK_WEIGHT = {"low": 1, "medium": 2, "high": 3}

_APPROVAL_REASON = {
    "all": "Approval policy requires approval for every action.",
    "high_only": "Approval policy requires approval for high-risk actions.",
    "medium_and_high": "Approval policy requires approval for medium and high-risk actions.",
}


def _max_risk(a: str, b: str) -> str:
    return a if RISK_WEIGHT[a] >= RISK_WEIGHT[b] else b


class ActionExecutor:
    def __init__(self, registry: ToolRegistry, approvals: ApprovalManager, settings: Settings) -> None:
        self.registry = registry
        self.approvals = approvals
        self.settings = settings

    def _requires_approval(self, risk: str) -> bool:
        policy = self.settings.approval_policy.lower().strip()
        if policy == "all":
            return True
        if policy == "high_only":
            return risk == "high"
        if policy == "none":
            return False
        return risk in {"medium", "high"}

    def _approval_reason(self) -> str:
        return _APPROVAL_REASON.get(self.settings.approval_policy.lower().strip(), "Approval required.")

    def _call_with_timeout(self, name: str, args: dict, timeout_seconds: float | None) -> dict:
        if not timeout_seconds or timeout_seconds <= 0:
            return self.registry.execute(name, args)
        completed = threading.Event()
        outcome: dict = {}
        error: BaseException | None = None

        def worker() -> None:
            nonlocal error
            try:
                outcome.update(self.registry.execute(name, args))
            except BaseException as exc:  # noqa: BLE001 - propagated to caller below
                error = exc
            finally:
                completed.set()

        thread = threading.Thread(target=worker, daemon=True, name=f"jarvis-tool-{name}")
        thread.start()
        if not completed.wait(timeout=timeout_seconds):
            # The worker may still be running; the caller has moved on.
            raise RuntimeError(f"tool '{name}' timed out after {timeout_seconds:g}s")
        if error is not None:
            raise error
        return outcome

    def execute(self, command: str, action: Action, auto_approve: bool = False) -> ToolExecutionResult:
        tool = self.registry.get(action.tool)
        if tool is None:
            return ToolExecutionResult(
                tool=action.tool,
                status="failed",
                risk=action.risk,
                category="unknown",
                error=f"Unknown tool: {action.tool}",
            )

        effective_risk = cast(RiskLevel, _max_risk(action.risk, tool.risk))
        base_meta = {
            "tool_category": tool.category,
            "read_write": tool.read_write,
            "external_action": tool.external_action,
        }

        if self._requires_approval(effective_risk) and not auto_approve:
            approval_id = self.approvals.create(command=command, action=action.model_copy(update={"risk": effective_risk}))
            return ToolExecutionResult(
                tool=action.tool,
                status="pending",
                risk=effective_risk,
                category=tool.category,
                output={"message": "Approval required before execution."},
                success=False,
                metadata={**base_meta, "requires_approval": True},
                requires_approval=True,
                approval_reason=self._approval_reason(),
                pending_approval_id=approval_id,
            )

        retry_attempts = max(0, int(getattr(self.settings, "tool_retry_attempts", 1)))
        max_attempts = 1 + retry_attempts
        retry_backoff = float(getattr(self.settings, "tool_retry_backoff_seconds", 0.4))
        started = time.perf_counter()

        last_error: str | None = None
        timed_out = False
        for attempt in range(1, max_attempts + 1):
            try:
                output = self._call_with_timeout(action.tool, action.args, tool.timeout_seconds)
                payload = dict(output or {})
                duration_ms = int((time.perf_counter() - started) * 1000)
                metadata = {
                    **base_meta,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "duration_ms": duration_ms,
                }
                if tool.timeout_seconds:
                    metadata["timeout_seconds"] = tool.timeout_seconds
                return ToolExecutionResult(
                    tool=action.tool,
                    status="completed",
                    risk=effective_risk,
                    category=tool.category,
                    output=payload,
                    metadata=metadata,
                    success=True,
                )
            except Exception as exc:
                last_error = str(exc)
                timed_out = "timed out" in last_error
                is_last = attempt >= max_attempts
                if is_last or effective_risk == "high" or timed_out:
                    break
                sleep_seconds = max(0.0, retry_backoff * attempt)
                time.sleep(sleep_seconds)

        duration_ms = int((time.perf_counter() - started) * 1000)
        return ToolExecutionResult(
            tool=action.tool,
            status="failed",
            risk=effective_risk,
            category=tool.category,
            error=last_error,
            success=False,
            metadata={
                **base_meta,
                "attempt": max_attempts if not timed_out else 1,
                "max_attempts": max_attempts,
                "duration_ms": duration_ms,
                "timed_out": timed_out,
            },
        )
