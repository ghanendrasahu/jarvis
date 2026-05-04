from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timezone
from typing import Iterable

from app.config import Settings
from app.core.approvals import ApprovalManager
from app.core.executor import ActionExecutor
from app.core.memory import MemoryStore
from app.core.router import DirectCommandRouter
from app.llm.planner import BasePlanner
from app.models.schemas import (
    ExecuteApprovalResponse,
    ExecuteCommandResponse,
    Plan,
    ToolExecutionResult,
)
from app.tools.registry import ToolRegistry


class JarvisOrchestrator:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        router: DirectCommandRouter,
        planner: BasePlanner,
        executor: ActionExecutor,
        approvals: ApprovalManager,
        memory: MemoryStore,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.router = router
        self.planner = planner
        self.executor = executor
        self.approvals = approvals
        self.memory = memory

    @staticmethod
    def _status_from_results(results: Iterable[ToolExecutionResult]) -> str:
        results_list = list(results)
        if not results_list:
            return "completed"
        has_pending = any(item.status == "pending" for item in results_list)
        has_failed = any(item.status == "failed" for item in results_list)
        has_completed = any(item.status == "completed" for item in results_list)
        if has_pending and has_completed:
            return "partial"
        if has_pending:
            return "needs_approval"
        if has_failed and has_completed:
            return "partial"
        if has_failed:
            return "failed"
        return "completed"

    @staticmethod
    def _compose_response(plan: Plan, results: list[ToolExecutionResult]) -> str:
        if not results:
            return plan.final_response or "No executable actions were generated."

        completed = [item for item in results if item.status == "completed"]
        pending = [item for item in results if item.status == "pending"]
        failed = [item for item in results if item.status == "failed"]

        lines: list[str] = []
        if plan.final_response:
            lines.append(plan.final_response)
        include_completed_line = True
        if len(completed) == 1 and not pending and not failed:
            item = completed[0]
            if item.tool == "get_current_time":
                time_12h = item.output.get("time_12h")
                weekday = item.output.get("weekday")
                date_text = item.output.get("date")
                if time_12h and weekday and date_text:
                    lines.append(f"Current time is {time_12h} on {weekday}, {date_text}.")
                    include_completed_line = False
            elif item.tool == "open_application":
                target = item.output.get("target")
                kind = item.output.get("kind")
                if target and kind == "url":
                    lines.append(f"Opened {target} in browser.")
                    include_completed_line = False
                elif target:
                    lines.append(f"Opened {target}.")
                    include_completed_line = False

        if completed and include_completed_line:
            lines.append(f"Completed: {', '.join(item.tool for item in completed)}.")
        if pending:
            approvals = ", ".join(
                f"{item.tool} (id: {item.pending_approval_id})" for item in pending if item.pending_approval_id
            )
            lines.append(f"Pending approval: {approvals}.")
        if failed:
            errors = ", ".join(
                f"{item.tool}: {item.error}" for item in failed if item.error
            )
            lines.append(f"Failures: {errors}.")
        return " ".join(lines).strip()

    @staticmethod
    def _instant_reply(command: str) -> str | None:
        lowered = command.lower().strip()
        if lowered in {"hi", "hello", "hey", "yo", "jarvis"}:
            return "Online and ready."
        if lowered in {"open", "start", "launch"}:
            return "Tell me what to open. Example: 'open chrome' or 'open whatsapp'."
        if lowered in {"close", "quit", "kill"}:
            return "Tell me what to close. Example: 'close chrome'."
        if lowered in {"help", "what can you do", "what can you do?"}:
            return (
                "I can run system commands, open apps/sites, automate files, and execute voice-triggered tasks. "
                "Try: 'system status', 'open chrome', 'search web ...', 'find files *.pdf'."
            )
        if lowered in {"thanks", "thank you"}:
            return "Anytime."
        if lowered.startswith("id:"):
            return "Use '/approve <id>' to approve pending actions."
        return None

    @staticmethod
    def _infer_surface_from_result(item: ToolExecutionResult) -> str | None:
        if item.status != "completed":
            return None
        if item.tool in {"open_youtube_video", "control_youtube", "seek_youtube"}:
            return "youtube"
        if item.tool == "open_whatsapp_chat":
            return "whatsapp"
        if item.tool == "open_url":
            target = str(item.output.get("opened") or "")
            host = urllib.parse.urlparse(target).netloc.lower()
            if "youtube.com" in host or "youtu.be" in host:
                return "youtube"
            if "whatsapp.com" in host:
                return "whatsapp"
        return None

    def _load_runtime_context(self) -> dict:
        return self.memory.get_state("runtime_context", default={})

    def _save_runtime_context(self, payload: dict) -> None:
        self.memory.set_state("runtime_context", payload)

    def _update_runtime_context(self, command: str, response_status: str, results: list[ToolExecutionResult]) -> None:
        prior = self._load_runtime_context()
        surface = None
        for item in results:
            inferred = self._infer_surface_from_result(item)
            if inferred:
                surface = inferred
        updated = {
            **prior,
            "last_command": command,
            "last_status": response_status,
            "last_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if surface:
            updated["active_surface"] = surface

        for item in results:
            if item.status != "completed":
                continue
            if item.tool == "open_whatsapp_chat":
                phone = item.output.get("phone")
                if phone:
                    updated["last_whatsapp_phone"] = phone
            if item.tool in {"open_youtube_video", "seek_youtube", "control_youtube"}:
                url = item.output.get("opened") or item.output.get("url") or item.output.get("navigated_to")
                if url:
                    updated["last_youtube_url"] = str(url)
        self._save_runtime_context(updated)

    def execute_command(self, command: str, source: str = "api", auto_approve: bool = False) -> ExecuteCommandResponse:
        started = time.perf_counter()
        cleaned = command.strip()
        if not cleaned:
            return ExecuteCommandResponse(
                command=command,
                source=source,
                status="failed",
                response_text="Command is empty.",
                plan_summary="",
                results=[],
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        quick_reply = self._instant_reply(cleaned)
        if quick_reply is not None:
            status = "completed"
            payload = ExecuteCommandResponse(
                command=cleaned,
                source=source,
                status=status,
                response_text=quick_reply,
                plan_summary="Instant response",
                results=[],
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self.memory.log_command(source=source, command=cleaned, response_text=quick_reply)
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.memory.log_execution_metric(
                source=source,
                command=cleaned,
                status=status,
                duration_ms=duration_ms,
                actions_total=0,
                actions_completed=0,
                actions_failed=0,
                actions_pending=0,
            )
            return payload

        context = self._load_runtime_context()
        direct_actions = self.router.route(cleaned, context=context)
        if direct_actions:
            plan = Plan(summary="Direct command route", actions=direct_actions, final_response="")
        else:
            history = self.memory.recent_conversation(limit=self.settings.max_history_items)
            plan = self.planner.plan(cleaned, self.registry.catalog(), history)

        valid_tools = {item["name"] for item in self.registry.catalog()}
        filtered_actions = [action for action in plan.actions if action.tool in valid_tools]
        if len(filtered_actions) != len(plan.actions):
            plan = plan.model_copy(
                update={
                    "actions": filtered_actions,
                    "final_response": (
                        (plan.final_response + " ") if plan.final_response else ""
                    )
                    + "Some requested actions were ignored because they used unavailable tools.",
                }
            )

        results: list[ToolExecutionResult] = []
        for action in plan.actions:
            allow_auto = auto_approve or (self.settings.auto_approve_low_risk and action.risk == "low")
            outcome = self.executor.execute(command=cleaned, action=action, auto_approve=allow_auto)
            results.append(outcome)
            self.memory.log_tool_result(
                command=cleaned,
                tool=outcome.tool,
                status=outcome.status,
                risk=outcome.risk,
                output=outcome.output,
                error=outcome.error,
            )

        response_text = self._compose_response(plan, results)
        status = self._status_from_results(results)
        self._update_runtime_context(command=cleaned, response_status=status, results=results)

        duration_ms = int((time.perf_counter() - started) * 1000)
        completed_count = sum(1 for item in results if item.status == "completed")
        failed_count = sum(1 for item in results if item.status == "failed")
        pending_count = sum(1 for item in results if item.status == "pending")
        self.memory.log_execution_metric(
            source=source,
            command=cleaned,
            status=status,
            duration_ms=duration_ms,
            actions_total=len(results),
            actions_completed=completed_count,
            actions_failed=failed_count,
            actions_pending=pending_count,
        )

        payload = ExecuteCommandResponse(
            command=cleaned,
            source=source,
            status=status,  # type: ignore[arg-type]
            response_text=response_text,
            plan_summary=plan.summary,
            results=results,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.memory.log_command(source=source, command=cleaned, response_text=response_text)
        return payload

    def approve(self, approval_id: str) -> ExecuteApprovalResponse:
        pending = self.approvals.approve(approval_id)
        if not pending:
            return ExecuteApprovalResponse(status="not_found", message="Approval ID is invalid or expired.")

        started = time.perf_counter()
        outcome = self.executor.execute(command=pending.command, action=pending.action, auto_approve=True)
        self.memory.log_tool_result(
            command=pending.command,
            tool=outcome.tool,
            status=outcome.status,
            risk=outcome.risk,
            output=outcome.output,
            error=outcome.error,
        )
        execution = ExecuteCommandResponse(
            command=pending.command,
            source="approval",
            status="completed" if outcome.status == "completed" else "failed",
            response_text=(
                f"Approved and executed {outcome.tool}."
                if outcome.status == "completed"
                else f"Approval execution failed: {outcome.error}"
            ),
            plan_summary=f"Approved action: {outcome.tool}",
            results=[outcome],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.memory.log_command(source="approval", command=pending.command, response_text=execution.response_text)
        status = "completed" if outcome.status == "completed" else "failed"
        self._update_runtime_context(command=pending.command, response_status=status, results=[outcome])
        self.memory.log_execution_metric(
            source="approval",
            command=pending.command,
            status=status,
            duration_ms=int((time.perf_counter() - started) * 1000),
            actions_total=1,
            actions_completed=1 if outcome.status == "completed" else 0,
            actions_failed=1 if outcome.status == "failed" else 0,
            actions_pending=0,
        )
        return ExecuteApprovalResponse(status="approved", message="Approval executed.", execution=execution)

    def list_pending_approvals(self):
        return self.approvals.list_pending()

    def get_history(self, limit: int = 30) -> list[dict]:
        return self.memory.recent_conversation(limit=limit)

    def get_tool_history(self, limit: int = 50) -> list[dict]:
        return self.memory.recent_tool_history(limit=limit)

    def get_metrics(self, limit: int = 200) -> dict:
        return {
            "summary": self.memory.metrics_summary(limit=limit),
            "recent": self.memory.recent_execution_metrics(limit=min(limit, 500)),
            "context": self.memory.get_state("runtime_context", default={}),
        }
