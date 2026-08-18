from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.executor import ActionExecutor
from app.core.logging import get_logger
from app.core.memory import MemoryStore
from app.core.prompts import build_agent_observation
from app.llm.planner import BasePlanner
from app.models.schemas import ToolExecutionResult
from app.tools.registry import ToolRegistry

logger = get_logger("agent")


@dataclass
class AgentRunResult:
    command: str
    status: str  # completed / needs_approval / failed / partial
    final_response: str
    iterations: int
    actions_total: int
    results: list[ToolExecutionResult] = field(default_factory=list)
    stopped_reason: str | None = None


class AutonomousAgent:
    """Plan -> execute -> observe -> re-plan loop with guardrails."""

    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        planner: BasePlanner,
        executor: ActionExecutor,
        approvals: ApprovalManager,
        memory: MemoryStore,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.planner = planner
        self.executor = executor
        self.approvals = approvals
        self.memory = memory

    def run(
        self,
        command: str,
        source: str = "agent",
        auto_approve: bool = False,
        max_iterations: int | None = None,
        action_cap: int | None = None,
    ) -> AgentRunResult:
        max_iterations = max(1, int(max_iterations or self.settings.agent_max_iterations))
        action_cap = max(1, int(action_cap or self.settings.agent_action_cap))
        catalog = self.registry.catalog()
        history = self.memory.recent_conversation(limit=self.settings.max_history_items)

        all_results: list[ToolExecutionResult] = []
        observation_rows: list[dict[str, Any]] = []
        failed_counts: dict[str, int] = {}
        stopped_reason: str | None = None
        iterations = 0
        action_total = 0
        final_response = ""

        for iteration in range(1, max_iterations + 1):
            iterations = iteration
            remaining = action_cap - action_total
            if remaining <= 0:
                stopped_reason = "reached action cap"
                break

            agent_context = build_agent_observation(observation_rows)
            try:
                plan = self.planner.plan(command, catalog, history, agent_context=agent_context)
            except Exception as exc:
                logger.warning("planner call failed on iteration %d: %s", iteration, exc)
                stopped_reason = "planner_error"
                final_response = f"Planning failed: {exc}"
                break

            if not plan.actions:
                stopped_reason = "planner returned no actions"
                final_response = plan.final_response or "No further actions were planned."
                break

            completed_now = 0
            for action in plan.actions:
                if action.tool == "agent_done":
                    stopped_reason = "task_complete"
                    final_response = str(action.args.get("summary") or plan.final_response or "Task complete.")
                    break

                if action_total >= action_cap:
                    stopped_reason = "reached action cap"
                    break

                action_total += 1
                allow_auto = auto_approve or (self.settings.auto_approve_low_risk and action.risk == "low")
                outcome = self.executor.execute(command=command, action=action, auto_approve=allow_auto)
                all_results.append(outcome)
                self.memory.log_tool_result(
                    command=command,
                    tool=outcome.tool,
                    status=outcome.status,
                    risk=outcome.risk,
                    output=outcome.output,
                    error=outcome.error,
                )
                observation_rows.append(
                    {
                        "tool": outcome.tool,
                        "status": outcome.status,
                        "output": outcome.output,
                        "error": outcome.error,
                    }
                )

                if outcome.status == "failed":
                    failed_counts[outcome.tool] = failed_counts.get(outcome.tool, 0) + 1
                    if failed_counts[outcome.tool] >= 2:
                        stopped_reason = f"tool '{outcome.tool}' failed twice; stopping"
                        break
                elif outcome.status == "pending":
                    stopped_reason = "approval_required"
                    break
                elif outcome.status == "completed":
                    completed_now += 1

            if stopped_reason is not None:
                break

            if completed_now == 0:
                stopped_reason = "no progress in iteration"
                if not final_response:
                    final_response = "No progress could be made; stopping early."
                break

            logger.info("agent iteration=%d actions=%d", iteration, action_total)

        results_summary = self._status(all_results)
        status = "completed"
        if stopped_reason == "approval_required":
            status = "needs_approval"
        elif stopped_reason == "planner_error":
            status = "failed"
        elif stopped_reason in {"tool '...' failed twice; stopping", "no progress in iteration"} or any(
            item.status == "failed" for item in all_results
        ):
            status = "failed" if all(item.status == "failed" for item in all_results) and all_results else "partial"
        elif stopped_reason == "task_complete":
            status = "completed"
        elif stopped_reason in {"reached action cap", "planner returned no actions"}:
            status = "partial"

        if not final_response:
            final_response = results_summary

        return AgentRunResult(
            command=command,
            status=status,
            final_response=final_response,
            iterations=iterations,
            actions_total=action_total,
            results=all_results,
            stopped_reason=stopped_reason,
        )

    @staticmethod
    def _status(results: list[ToolExecutionResult]) -> str:
        if not results:
            return "Completed with no tool executions."
        completed = sum(1 for item in results if item.status == "completed")
        pending = sum(1 for item in results if item.status == "pending")
        failed = sum(1 for item in results if item.status == "failed")
        return f"Completed {completed} action(s), {failed} failed, {pending} pending approval."
