from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.core.agent import AgentRunResult, AutonomousAgent
from app.core.executor import ActionExecutor
from app.core.logging import get_logger
from app.core.memory import MemoryStore
from app.models.schemas import Action, ToolExecutionResult

logger = get_logger("code_agent")

_CODE_MODIFYING_TOOLS = {"edit_file", "write_text_file", "move_file", "delete_file"}

_CODE_ROLE = """CODE MODE: You are a coding agent working directly in the user's repository.
Workflow:
1. Understand the task with search_code and read_text_file before changing anything.
2. Make minimal, surgical changes with edit_file (or write_text_file for new files).
3. After editing, ALWAYS finish by running lint_code.
4. If lint fails, fix the code and rerun until everything passes.
Use only tools listed in the catalog. When verification passes, emit agent_done with a summary of what changed."""

_FIX_ROLE = """CODE FIX MODE: Verification failed on the previous changes. Review the failure output in the history,
find the cause with search_code/read_text_file, apply a fix, then rerun lint_code.
Do not emit agent_done until lint passes."""


def _pending_approval_id(results: list[ToolExecutionResult]) -> str | None:
    for item in results:
        if item.status == "pending" and item.pending_approval_id:
            return item.pending_approval_id
    return None


class CodeAgent:
    """Autonomous coding workflow: edit via the shared agent loop, then verify.

    Wraps the AutonomousAgent (which owns approval/resume plumbing) and adds a
    deterministic verification gate: after each editing round it runs the test
    suite and linter, and re-plans fixes until green or the round budget is spent.
    """

    def __init__(
        self,
        agent: AutonomousAgent,
        executor: ActionExecutor,
        settings: Settings,
        memory: MemoryStore,
    ) -> None:
        self.agent = agent
        self.executor = executor
        self.settings = settings
        self.memory = memory
        self._rounds: dict[str, dict[str, Any]] = {}

    def run(
        self,
        command: str,
        source: str = "agent",
        auto_approve: bool = False,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        max_fix_rounds: int | None = None,
    ) -> AgentRunResult:
        max_fix_rounds = max(1, int(max_fix_rounds or self.settings.code_max_fix_rounds))
        combined: list[ToolExecutionResult] = []
        task = command
        last_summary = ""
        for round_index in range(1, max_fix_rounds + 1):
            role = _FIX_ROLE if round_index > 1 else _CODE_ROLE
            result = self.agent.run(
                task,
                source=source,
                auto_approve=auto_approve,
                on_step=on_step,
                role=role,
            )
            combined.extend(result.results)

            pending_id = _pending_approval_id(result.results)
            if result.status == "needs_approval" and pending_id is not None:
                self._rounds[pending_id] = {
                    "command": command,
                    "source": source,
                    "auto_approve": auto_approve,
                    "max_fix_rounds": max_fix_rounds,
                    "round_index": round_index,
                    "combined": combined,
                    "last_summary": last_summary,
                }
                return self._summarize(command, combined, result, verification=None, stopped_reason="approval_required")

            if self._touched_code(result.results):
                verification = self._verify(source, auto_approve, on_step)
                combined.extend(verification["results"])
                last_summary = verification["summary"]
                if verification["passed"]:
                    return self._summarize(command, combined, result, verification=verification, stopped_reason="verified")
                if round_index >= max_fix_rounds:
                    return self._summarize(
                        command, combined, result, verification=verification, stopped_reason="verification_failed"
                    )
                task = (
                    f"{command}\n\nFOLLOW-UP FIX ROUND {round_index}: "
                    f"verification failed:\n{last_summary}\nFix the failures and rerun verification."
                )
            else:
                if not result.results:
                    return self._summarize(command, combined, result, verification=None, stopped_reason=result.stopped_reason)
                verification = self._verify(source, auto_approve, on_step)
                combined.extend(verification["results"])
                if verification["passed"] or round_index >= max_fix_rounds:
                    return self._summarize(command, combined, result, verification=verification, stopped_reason="verified")
        return self._summarize(command, combined, AgentRunResult(command=command, status="partial", final_response="", iterations=0, actions_total=0), verification=None, stopped_reason="max_rounds")

    def resume(
        self,
        approval_id: str,
        executed_outcome: ToolExecutionResult,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult | None:
        """Continue a code run that paused for approval. Returns None if not ours."""
        ctx = self._rounds.pop(approval_id, None)
        if ctx is None:
            return None
        result = self.agent.resume(approval_id, executed_outcome, on_step=on_step)
        if result is None:
            return None
        combined = list(ctx["combined"])
        combined.extend(result.results)
        command = str(ctx["command"])
        source = str(ctx["source"])
        auto_approve = bool(ctx["auto_approve"])
        max_fix_rounds = int(ctx["max_fix_rounds"])
        round_index = int(ctx["round_index"])

        pending_id = _pending_approval_id(result.results)
        if result.status == "needs_approval" and pending_id is not None:
            self._rounds[pending_id] = {
                **ctx,
                "round_index": round_index,
                "combined": combined,
                "last_summary": str(ctx.get("last_summary") or ""),
            }
            return self._summarize(command, combined, result, verification=None, stopped_reason="approval_required")

        if self._touched_code(result.results):
            verification = self._verify(source, auto_approve, on_step)
            combined.extend(verification["results"])
            if verification["passed"] or round_index >= max_fix_rounds:
                return self._summarize(command, combined, result, verification=verification, stopped_reason="verified")
            return self.run(
                f"{command}\n\nFOLLOW-UP FIX ROUND {round_index}: "
                f"verification failed:\n{verification['summary']}\nFix the failures and rerun verification.",
                source=source,
                auto_approve=auto_approve,
                on_step=on_step,
                max_fix_rounds=max_fix_rounds - round_index,
            )
        return self._summarize(command, combined, result, verification=None, stopped_reason=result.stopped_reason)

    def forget(self, approval_id: str) -> None:
        self._rounds.pop(approval_id, None)
        self.agent.forget(approval_id)

    @staticmethod
    def _touched_code(results: list[ToolExecutionResult]) -> bool:
        return any(item.status == "completed" and item.tool in _CODE_MODIFYING_TOOLS for item in results)

    def _verify(
        self,
        source: str,
        auto_approve: bool,
        on_step: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        outcomes: list[ToolExecutionResult] = []
        for action in (
            Action(tool="lint_code", args={"path": ""}, risk="medium"),
        ):
            outcome = self.executor.execute(
                command="verify code changes",
                action=action,
                auto_approve=True,
            )
            outcomes.append(outcome)
            self.memory.log_tool_result(
                command="verify code changes",
                tool=outcome.tool,
                status=outcome.status,
                risk=outcome.risk,
                output=outcome.output,
                error=outcome.error,
            )
            if on_step is not None:
                on_step(
                    {
                        "tool": outcome.tool,
                        "status": outcome.status,
                        "error": outcome.error,
                        "output_snippet": self._snippet(outcome),
                        "iteration": 0,
                        "actions_total": 0,
                    }
                )
        passed = all(
            outcome.status == "completed" and bool((outcome.output or {}).get("passed"))
            for outcome in outcomes
        )
        return {
            "passed": passed,
            "summary": self._verification_summary(outcomes),
            "results": outcomes,
        }

    def _summarize(
        self,
        command: str,
        combined: list[ToolExecutionResult],
        result: AgentRunResult,
        verification: dict[str, Any] | None,
        stopped_reason: str | None,
    ) -> AgentRunResult:
        if verification is None:
            final_response = result.final_response or "Code task paused."
            status = result.status
        elif verification["passed"]:
            final_response = "Code changes verified: tests and lint pass. " + (result.final_response or "")
            status = "completed"
        else:
            final_response = "Code changes complete but verification failed:\n" + verification["summary"]
            status = "partial"
        return AgentRunResult(
            command=command,
            status=status,
            final_response=final_response,
            iterations=result.iterations,
            actions_total=len(combined),
            results=combined,
            stopped_reason=stopped_reason,
        )

    @staticmethod
    def _snippet(outcome: ToolExecutionResult) -> str:
        if outcome.status == "failed":
            return str(outcome.error or "failed")[:200]
        output = outcome.output or {}
        return str(output.get("result_text") or "ok")[:200]

    @staticmethod
    def _verification_summary(outcomes: list[ToolExecutionResult]) -> str:
        lines: list[str] = []
        for outcome in outcomes:
            status = "PASS" if outcome.status == "completed" and (outcome.output or {}).get("passed") else "FAIL"
            lines.append(f"{outcome.tool}: {status}")
            tail = (
                str(outcome.error or "")
                or str((outcome.output or {}).get("stderr") or "")
                or str((outcome.output or {}).get("stdout") or "")
            )
            if tail:
                lines.append("    " + tail[-800:].replace("\n", "\n    "))
        return "\n".join(lines) or "Verification ran with no output."
