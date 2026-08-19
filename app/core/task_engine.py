from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from app.config import Settings
from app.core.events import EventHub
from app.core.executor import ActionExecutor
from app.core.logging import get_logger
from app.core.memory import MemoryStore
from app.models.schemas import Action, RiskLevel, ToolExecutionResult

logger = get_logger("task_engine")

StepStatus = Literal["pending", "running", "completed", "failed", "canceled"]
TaskStatus = Literal["pending", "running", "completed", "failed", "partial", "needs_approval", "canceled"]

_TASK_STATE_KEY = "task_index"


class TaskStep(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: str = "low"
    reason: str = ""
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    category: str = "general"
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    pending_approval_id: str | None = None
    attempts: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None


class Task(BaseModel):
    id: str
    command: str
    source: str = "api"
    status: TaskStatus = "pending"
    steps: list[TaskStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def progress(self) -> dict[str, Any]:
        total = len(self.steps)
        completed = sum(1 for step in self.steps if step.status == "completed")
        failed = sum(1 for step in self.steps if step.status == "failed")
        pending = sum(1 for step in self.steps if step.status == "pending")
        current = next((step.tool for step in self.steps if step.status == "running"), None)
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "current_step": current,
            "percent": round((completed / total) * 100, 1) if total else 0.0,
        }


class TaskEngine:
    """Persistent, resumable multi-step goal execution over the shared executor.

    A Task is a list of dependent steps. Steps execute through the same
    risk-gated executor as direct commands, so permissions, retries, and
    per-tool timeouts are identical. Tasks can be cancelled, resumed, listed,
    and resumed after an approval is granted.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        memory: MemoryStore,
        settings: Settings,
        events: EventHub | None = None,
    ) -> None:
        self.executor = executor
        self.memory = memory
        self.settings = settings
        self.events = events

    # ---- lifecycle ---------------------------------------------------------

    def create(self, command: str, actions: list[Action], source: str = "api") -> Task:
        command = (command or "").strip()
        task = Task(
            id=str(uuid.uuid4())[:8],
            command=command,
            source=source,
            status="pending",
            steps=[
                TaskStep(
                    id=f"st-{index:02d}",
                    tool=action.tool,
                    args=dict(action.args or {}),
                    risk=action.risk,
                    reason=action.reason,
                    category="general",
                    depends_on=[],
                )
                for index, action in enumerate(actions or [])
            ],
        )
        self._save(task)
        self._index_add(task.id)
        return task

    def run(
        self,
        task_id: str,
        auto_approve: bool = False,
        on_step=None,
        timeout_seconds: float | None = None,
    ) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.status in {"running", "completed"}:
            return task
        task.timeout_seconds = timeout_seconds
        task.status = "running"
        task.started_at = task.started_at or datetime.now(UTC).isoformat()
        task.error = None
        self._emit("task_start", {"task_id": task.id, "command": task.command, "source": task.source})
        self._save(task)

        started = time.monotonic()
        for step in task.steps:
            if step.status in {"completed", "running"}:
                continue
            if self._cancelled(task):
                step.status = "canceled"
                continue
            if timeout_seconds and (time.monotonic() - started) > timeout_seconds:
                for remaining in task.steps:
                    if remaining.status == "pending":
                        remaining.status = "canceled"
                task.error = f"task timed out after {timeout_seconds:g}s"
                break
            if not self._deps_met(task, step):
                step.status = "canceled"
                continue

            step.status = "running"
            step.started_at = datetime.now(UTC).isoformat()
            step.attempts += 1
            self._save(task)

            allow_auto = auto_approve or (self.settings.auto_approve_low_risk and step.risk == "low")
            action = Action(
                tool=step.tool,
                args=dict(step.args or {}),
                risk=cast(RiskLevel, step.risk),
                reason=step.reason,
            )
            outcome = self.executor.execute(command=task.command, action=action, auto_approve=allow_auto)

            step.status = outcome.status  # type: ignore[assignment]
            step.output = dict(outcome.output or {})
            step.error = outcome.error
            step.pending_approval_id = outcome.pending_approval_id
            step.category = outcome.category
            step.metadata = dict(outcome.metadata or {})
            step.finished_at = datetime.now(UTC).isoformat()
            self._save(task)

            self._emit(
                "task_step",
                {
                    "task_id": task.id,
                    "step_id": step.id,
                    "tool": step.tool,
                    "status": step.status,
                    "error": step.error,
                },
            )
            if on_step is not None:
                on_step(
                    {
                        "task_id": task.id,
                        "tool": step.tool,
                        "status": step.status,
                        "error": step.error,
                        "output_snippet": self._snippet(step),
                    }
                )

        self._finalize(task)
        self._save(task)
        self._emit(
            "task_done",
            {
                "task_id": task.id,
                "command": task.command,
                "status": task.status,
                "progress": task.progress,
            },
        )
        return task

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        self.memory.set_state(self._cancel_key(task_id), True)
        if task.status == "pending":
            task.status = "canceled"
            for step in task.steps:
                if step.status == "pending":
                    step.status = "canceled"
            task.finished_at = datetime.now(UTC).isoformat()
            self._save(task)
        return True

    def resume(self, task_id: str, auto_approve: bool = False, on_step=None) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None
        self.memory.set_state(self._cancel_key(task_id), False)
        if task.status == "canceled":
            task.status = "pending"
        return self.run(task_id, auto_approve=auto_approve, on_step=on_step, timeout_seconds=task.timeout_seconds)

    def resume_after_approval(self, approval_id: str, outcome: ToolExecutionResult) -> bool:
        """Apply an approved/rejected outcome to any task step waiting on it."""
        for task in self.list(limit=200):
            for step in task.steps:
                if step.pending_approval_id != approval_id:
                    continue
                step.status = outcome.status  # type: ignore[assignment]
                step.output = dict(outcome.output or {})
                step.error = outcome.error
                step.pending_approval_id = None
                step.metadata = dict(outcome.metadata or {})
                step.finished_at = datetime.now(UTC).isoformat()
                self._finalize(task)
                self._save(task)
                self._emit(
                    "task_step",
                    {"task_id": task.id, "step_id": step.id, "tool": step.tool, "status": step.status},
                )
                return True
        return False

    # ---- query -------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        if not task_id:
            return None
        raw = self.memory.get_state(f"task:{task_id}", default=None)
        if not raw:
            return None
        try:
            return Task.model_validate(raw)
        except Exception:
            return None

    def list(self, limit: int = 50) -> list[Task]:
        ids = list(self.memory.get_state(_TASK_STATE_KEY, default=[]) or [])
        tasks: list[Task] = []
        for task_id in reversed(ids):
            task = self.get(str(task_id))
            if task is not None:
                tasks.append(task)
            if len(tasks) >= limit:
                break
        return tasks

    def delete(self, task_id: str) -> bool:
        ids = [str(item) for item in (self.memory.get_state(_TASK_STATE_KEY, default=[]) or [])]
        if task_id not in ids:
            return False
        self.memory.set_state(f"task:{task_id}", None)
        self.memory.set_state(self._cancel_key(task_id), False)
        ids.remove(task_id)
        self.memory.set_state(_TASK_STATE_KEY, ids)
        return True

    # ---- internals -----------------------------------------------------------

    @staticmethod
    def _cancel_key(task_id: str) -> str:
        return f"task_cancel:{task_id}"

    @staticmethod
    def _deps_met(task: Task, step: TaskStep) -> bool:
        return all(
            any(dep.id == step_id and dep.status == "completed" for dep in task.steps)
            for step_id in step.depends_on
        )

    def _cancelled(self, task: Task) -> bool:
        return bool(self.memory.get_state(self._cancel_key(task.id), default=False))

    def _finalize(self, task: Task) -> None:
        statuses = {step.status for step in task.steps}
        if self._cancelled(task) or task.status == "canceled":
            task.status = "canceled"
        elif "running" in statuses:
            task.status = "running"
        elif "pending" in statuses:
            task.status = "needs_approval"
        elif "failed" in statuses:
            task.status = "partial" if "completed" in statuses else "failed"
        elif "canceled" in statuses:
            task.status = "partial"
        elif statuses == {"completed"} or task.steps and statuses.issubset({"completed"}):
            task.status = "completed"
        elif not task.steps:
            task.status = "completed"
        task.finished_at = datetime.now(UTC).isoformat()

    @staticmethod
    def _snippet(step: TaskStep) -> str:
        if step.status == "failed":
            return str(step.error or "failed")[:200]
        if step.status == "pending":
            return "awaiting approval"
        output = step.output or {}
        for key in ("result_text", "message", "opened", "count", "url", "summary"):
            value = output.get(key)
            if value is not None:
                return str(value)[:200]
        return "ok"

    def _save(self, task: Task) -> None:
        self.memory.set_state(f"task:{task.id}", task.model_dump(mode="json"))

    def _index_add(self, task_id: str) -> None:
        ids = [str(item) for item in (self.memory.get_state(_TASK_STATE_KEY, default=[]) or [])]
        ids.append(task_id)
        self.memory.set_state(_TASK_STATE_KEY, ids[-200:])

    def _emit(self, topic: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(topic, payload)
