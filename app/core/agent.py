from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.contract import synthesize_result_text
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


@dataclass
class _LoopState:
    all_results: list[ToolExecutionResult]
    observation_rows: list[dict[str, Any]]
    failed_counts: dict[str, int]
    iterations: int
    action_total: int


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
        self._resume_tokens: dict[str, dict[str, Any]] = {}

    def run(
        self,
        command: str,
        source: str = "agent",
        auto_approve: bool = False,
        max_iterations: int | None = None,
        action_cap: int | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        role: str = "",
    ) -> AgentRunResult:
        max_iterations = max(1, int(max_iterations or self.settings.agent_max_iterations))
        action_cap = max(1, int(action_cap or self.settings.agent_action_cap))
        return self._loop(
            command=command,
            source=source,
            auto_approve=auto_approve,
            max_iterations=max_iterations,
            action_cap=action_cap,
            on_step=on_step,
            role=role,
            state=_LoopState(all_results=[], observation_rows=[], failed_counts={}, iterations=0, action_total=0),
        )

    def resume(
        self,
        approval_id: str,
        executed_outcome: ToolExecutionResult,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult | None:
        """Continue a run that paused for approval, using the stored resume token."""
        token = self._resume_tokens.pop(approval_id, None)
        if not token:
            return None
        state = _LoopState(
            all_results=[ToolExecutionResult(**item) for item in token.get("all_results") or []],
            observation_rows=list(token.get("observation_rows") or []),
            failed_counts=dict(token.get("failed_counts") or {}),
            iterations=int(token.get("iterations", 0)),
            action_total=int(token.get("action_total", 0)),
        )
        if state.all_results and state.all_results[-1].pending_approval_id == approval_id:
            state.all_results[-1] = executed_outcome
        else:
            state.all_results.append(executed_outcome)
        if state.observation_rows and state.observation_rows[-1].get("status") == "pending":
            state.observation_rows[-1] = {
                "tool": executed_outcome.tool,
                "status": executed_outcome.status,
                "output": executed_outcome.output,
                "error": executed_outcome.error,
            }
        else:
            state.observation_rows.append(
                {
                    "tool": executed_outcome.tool,
                    "status": executed_outcome.status,
                    "output": executed_outcome.output,
                    "error": executed_outcome.error,
                }
            )
        state.action_total += 1
        return self._loop(
            command=str(token.get("command") or ""),
            source=str(token.get("source") or "agent"),
            auto_approve=bool(token.get("auto_approve")),
            max_iterations=max(1, int(token.get("max_iterations") or self.settings.agent_max_iterations)),
            action_cap=max(1, int(token.get("action_cap") or self.settings.agent_action_cap)),
            on_step=on_step,
            role=str(token.get("role") or ""),
            state=state,
        )

    def forget(self, approval_id: str) -> None:
        self._resume_tokens.pop(approval_id, None)

    def _loop(
        self,
        command: str,
        source: str,
        auto_approve: bool,
        max_iterations: int,
        action_cap: int,
        on_step: Callable[[dict[str, Any]], None] | None,
        state: _LoopState,
        role: str = "",
    ) -> AgentRunResult:
        catalog = self.registry.catalog()
        history = self.memory.recent_conversation(limit=self.settings.max_history_items)
        tool_history = self.memory.recent_tool_history(limit=self.settings.max_history_items)
        known_facts = self.memory.get_state("runtime_context", default={}).get("learned_facts") or {}

        stopped_reason: str | None = None
        final_response = ""
        iterations = state.iterations

        for iteration in range(state.iterations + 1, max_iterations + 1):
            iterations = iteration
            remaining = action_cap - state.action_total
            if remaining <= 0:
                stopped_reason = "reached action cap"
                break

            agent_context = build_agent_observation(state.observation_rows)
            if role:
                agent_context = f"{role}\n\n{agent_context}" if agent_context else role
            try:
                plan = self.planner.plan(
                    command,
                    catalog,
                    history,
                    agent_context=agent_context,
                    tool_history=tool_history,
                    known_facts=known_facts,
                )
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

                if state.action_total >= action_cap:
                    stopped_reason = "reached action cap"
                    break

                state.action_total += 1
                allow_auto = auto_approve or (self.settings.auto_approve_low_risk and action.risk == "low")
                outcome = self.executor.execute(command=command, action=action, auto_approve=allow_auto)
                state.all_results.append(outcome)
                self.memory.log_tool_result(
                    command=command,
                    tool=outcome.tool,
                    status=outcome.status,
                    risk=outcome.risk,
                    output=outcome.output,
                    error=outcome.error,
                )
                state.observation_rows.append(
                    {
                        "tool": outcome.tool,
                        "status": outcome.status,
                        "output": outcome.output,
                        "error": outcome.error,
                    }
                )

                if on_step is not None:
                    on_step(
                        {
                            "tool": outcome.tool,
                            "status": outcome.status,
                            "error": outcome.error,
                            "output_snippet": self._snippet(outcome),
                            "iteration": iteration,
                            "actions_total": state.action_total,
                        }
                    )

                if outcome.status == "failed":
                    state.failed_counts[outcome.tool] = state.failed_counts.get(outcome.tool, 0) + 1
                    if state.failed_counts[outcome.tool] >= 2:
                        stopped_reason = f"tool '{outcome.tool}' failed twice; stopping"
                        break
                elif outcome.status == "pending":
                    if outcome.pending_approval_id:
                        self._resume_tokens[outcome.pending_approval_id] = {
                            "command": command,
                            "source": source,
                            "auto_approve": auto_approve,
                            "max_iterations": max_iterations,
                            "action_cap": action_cap,
                            "role": role,
                            "all_results": [item.model_dump() for item in state.all_results],
                            "observation_rows": state.observation_rows,
                            "failed_counts": state.failed_counts,
                            "action_total": state.action_total,
                            "iterations": iterations,
                        }
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

            logger.info("agent iteration=%d actions=%d", iteration, state.action_total)

        results_summary = self._status(state.all_results)
        status = "completed"
        if stopped_reason == "approval_required":
            status = "needs_approval"
        elif stopped_reason == "planner_error":
            status = "failed"
        elif stopped_reason in {"tool '...' failed twice; stopping", "no progress in iteration"} or any(
            item.status == "failed" for item in state.all_results
        ):
            status = "failed" if all(item.status == "failed" for item in state.all_results) and state.all_results else "partial"
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
            actions_total=state.action_total,
            results=state.all_results,
            stopped_reason=stopped_reason,
        )

    @staticmethod
    def _snippet(outcome: ToolExecutionResult) -> str:
        if outcome.status == "failed":
            return str(outcome.error or "failed")[:200]
        if outcome.status == "pending":
            return "awaiting approval"
        return synthesize_result_text(outcome.tool, outcome.output)[:200]

    @staticmethod
    def _status(results: list[ToolExecutionResult]) -> str:
        if not results:
            return "Completed with no tool executions."
        completed = sum(1 for item in results if item.status == "completed")
        pending = sum(1 for item in results if item.status == "pending")
        failed = sum(1 for item in results if item.status == "failed")
        return f"Completed {completed} action(s), {failed} failed, {pending} pending approval."
