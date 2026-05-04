from __future__ import annotations

import time

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.models.schemas import Action, ToolExecutionResult
from app.tools.registry import ToolRegistry

RISK_WEIGHT = {"low": 1, "medium": 2, "high": 3}


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

    def execute(self, command: str, action: Action, auto_approve: bool = False) -> ToolExecutionResult:
        tool = self.registry.get(action.tool)
        if tool is None:
            return ToolExecutionResult(
                tool=action.tool,
                status="failed",
                risk=action.risk,
                error=f"Unknown tool: {action.tool}",
            )

        effective_risk = _max_risk(action.risk, tool.risk)

        if self._requires_approval(effective_risk) and not auto_approve:
            approval_id = self.approvals.create(command=command, action=action.model_copy(update={"risk": effective_risk}))
            return ToolExecutionResult(
                tool=action.tool,
                status="pending",
                risk=effective_risk,
                output={"message": "Approval required before execution."},
                pending_approval_id=approval_id,
            )

        retry_attempts = max(0, int(getattr(self.settings, "tool_retry_attempts", 1)))
        max_attempts = 1 + retry_attempts
        retry_backoff = float(getattr(self.settings, "tool_retry_backoff_seconds", 0.4))

        last_error: str | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                output = self.registry.execute(action.tool, action.args)
                payload = dict(output or {})
                payload.setdefault(
                    "_execution",
                    {
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "risk": effective_risk,
                    },
                )
                return ToolExecutionResult(tool=action.tool, status="completed", risk=effective_risk, output=payload)
            except Exception as exc:
                last_error = str(exc)
                is_last = attempt >= max_attempts
                if is_last or effective_risk == "high":
                    break
                sleep_seconds = max(0.0, retry_backoff * attempt)
                time.sleep(sleep_seconds)

        return ToolExecutionResult(tool=action.tool, status="failed", risk=effective_risk, error=last_error)
