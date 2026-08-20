from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.core.calendar import CalendarStore
from app.core.memory import MemoryStore
from app.core.scheduler import Scheduler

if TYPE_CHECKING:
    from app.core.task_engine import TaskEngine
from app.tools.registry import ToolRegistry


@dataclass
class ParsedIntent:
    """Represents a parsed natural language intent for productivity operations."""
    action: str  # create_task, create_reminder, create_event, create_note, schedule, list, query
    entity: str  # task, reminder, event, note, schedule
    params: dict[str, Any]
    confidence: float
    raw_text: str
    confirmation_required: bool = False


class TaskPlanner:
    """Unified productivity planner with natural language understanding.

    Unifies tasks, reminders, calendar events, notes, and schedules
    behind a single natural language interface.
    """

    def __init__(
        self,
        settings: Settings,
        task_engine: TaskEngine | None = None,
        scheduler: Scheduler | None = None,
        calendar: CalendarStore | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.settings = settings
        self.task_engine = task_engine
        self.scheduler = scheduler
        self.calendar = calendar
        self.memory = memory

    def parse(self, text: str) -> ParsedIntent:
        """Parse natural language text into a structured intent."""
        text = text.strip().lower()

        # Task creation patterns
        task_patterns = [
            (r"(?:create|make|add)\s+(?:a\s+)?task\s+(?:to\s+|for\s+)?(.+)", "create_task"),
            (r"(?:i\s+need\s+to|i\s+want\s+to|i\s+have\s+to)\s+(.+)", "create_task"),
            (r"task:\s*(.+)", "create_task"),
        ]

        # Reminder patterns
        reminder_patterns = [
            (r"(?:set|create|add)\s+(?:a\s+)?reminder\s+(?:to\s+|for\s+)?(.+)", "create_reminder"),
            (r"remind\s+me\s+(?:to\s+)?(.+)", "create_reminder"),
            (r"reminder:\s*(.+)", "create_reminder"),
        ]

        # Calendar event patterns
        event_patterns = [
            (r"(?:create|add|schedule)\s+(?:an?\s+)?(?:event|meeting|appointment)\s+(.+)", "create_event"),
            (r"(?:put|add)\s+(.+)\s+on\s+(?:my\s+)?calendar", "create_event"),
            (r"event:\s*(.+)", "create_event"),
        ]

        # Note patterns
        note_patterns = [
            (r"(?:create|add|make|save)\s+(?:a\s+)?note\s+(.+)", "create_note"),
            (r"note:\s*(.+)", "create_note"),
            (r"remember\s+(.+)", "create_note"),
        ]

        # Schedule patterns
        schedule_patterns = [
            (r"(?:schedule|recurring)\s+(.+)", "create_schedule"),
            (r"(?:every|daily|weekly|hourly)\s+(.+)", "create_schedule"),
        ]

        # List/query patterns
        list_patterns = [
            (r"(?:show|list|what\s+are)\s+(?:my\s+)?(tasks?|reminders?|events?|notes?|schedules?)", "list"),
            (r"(?:what\s+do\s+i\s+have|what\s+is\s+on)\s+(?:my\s+)?(?:calendar|schedule|agenda)", "list_events"),
            (r"(?:what\s+tasks?|pending\s+tasks?)", "list_tasks"),
        ]

        # Query patterns
        query_patterns = [
            (r"(?:when\s+is|what\s+time)\s+(.+)", "query_time"),
            (r"(?:find|search)\s+(?:note|task|event)\s+(.+)", "search"),
        ]

        all_patterns = [
            (task_patterns, "create_task", "task"),
            (reminder_patterns, "create_reminder", "reminder"),
            (event_patterns, "create_event", "event"),
            (note_patterns, "create_note", "note"),
            (schedule_patterns, "create_schedule", "schedule"),
            (list_patterns, "list", None),
            (query_patterns, "query", None),
        ]

        best_match = None
        best_confidence = 0.0

        for patterns, _action, entity in all_patterns:
            for pattern, matched_action in patterns:
                match = re.match(pattern, text)
                if match:
                    confidence = 0.9
                    if len(match.groups()) > 0:
                        confidence = 0.95
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = (matched_action, entity, match)

        if best_match:
            action, entity, match = best_match
            params = {}
            if match.groups():
                params["description"] = match.group(1).strip()
            return ParsedIntent(
                action=action,
                entity=entity or "",
                params=params,
                confidence=best_confidence,
                raw_text=text,
                confirmation_required=self._requires_confirmation(action),
            )

        # Default: treat as note
        return ParsedIntent(
            action="create_note",
            entity="note",
            params={"description": text},
            confidence=0.3,
            raw_text=text,
            confirmation_required=False,
        )

    def _requires_confirmation(self, action: str) -> bool:
        """Determine if an action requires user confirmation."""
        return action in {"create_task", "create_event", "create_schedule", "create_reminder"}

    def execute(self, intent: ParsedIntent, confirm: bool = False) -> dict[str, Any]:
        """Execute a parsed intent."""
        if intent.confirmation_required and not confirm:
            return {
                "status": "pending_confirmation",
                "intent": {
                    "action": intent.action,
                    "entity": intent.entity,
                    "params": intent.params,
                    "raw_text": intent.raw_text,
                },
                "message": f"Confirm: {self._describe_intent(intent)}?",
            }

        try:
            if intent.action == "create_task":
                return self._create_task(intent.params)
            elif intent.action == "create_reminder":
                return self._create_reminder(intent.params)
            elif intent.action == "create_event":
                return self._create_event(intent.params)
            elif intent.action == "create_note":
                return self._create_note(intent.params)
            elif intent.action == "create_schedule":
                return self._create_schedule(intent.params)
            elif intent.action == "list":
                return self._list_all(intent.params)
            elif intent.action == "list_events":
                return self._list_events(intent.params)
            elif intent.action == "list_tasks":
                return self._list_tasks(intent.params)
            elif intent.action == "query_time":
                return self._query_time(intent.params)
            elif intent.action == "search":
                return self._search(intent.params)
            else:
                return {"status": "error", "message": f"Unknown action: {intent.action}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _describe_intent(self, intent: ParsedIntent) -> str:
        """Generate a human-readable description of the intent."""
        action_map = {
            "create_task": "Create task",
            "create_reminder": "Create reminder",
            "create_event": "Create calendar event",
            "create_note": "Create note",
            "create_schedule": "Create recurring schedule",
        }
        base = action_map.get(intent.action, intent.action)
        desc = intent.params.get("description", "")
        return f"{base}: {desc}" if desc else base

    def _create_task(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.task_engine:
            return {"status": "error", "message": "Task engine not available"}
        desc = params.get("description", "")
        if not desc:
            return {"status": "error", "message": "Task description required"}
        # Simple task with single announce step
        from app.models.schemas import Action
        task = self.task_engine.create(
            desc,
            [Action(tool="announce", args={"message": desc}, risk="low", reason="Task reminder")],
            source="planner",
        )
        return {"status": "success", "task_id": task.id, "message": f"Created task: {desc}"}

    def _create_reminder(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.scheduler:
            return {"status": "error", "message": "Scheduler not available"}
        desc = params.get("description", "")
        if not desc:
            return {"status": "error", "message": "Reminder description required"}
        # Parse time from description (simplified)
        when = "in 1 hour"  # default
        # Try to extract time from description
        time_match = re.search(r"(?:in|at|for)\s+(.+)$", params.get("description", ""))
        if time_match:
            when = time_match.group(1).strip()
        from app.core.scheduler import parse_reminder_when
        at = parse_reminder_when(when)
        job = self.scheduler.add_once(f"announce {desc}", at)
        return {
            "status": "success",
            "job_id": job["id"],
            "scheduled_for": at.isoformat(),
            "message": f"Reminder set for {at.isoformat()}: {desc}",
        }

    def _create_event(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.calendar:
            return {"status": "error", "message": "Calendar not available"}
        desc = params.get("description", "")
        if not desc:
            return {"status": "error", "message": "Event description required"}
        # Try to parse title and time from description
        # Default to 1 hour from now
        start = datetime.now(UTC) + timedelta(hours=1)
        event = self.calendar.add_event(desc, start.isoformat(), duration_minutes=60)
        return {
            "status": "success",
            "event_id": event["id"],
            "message": f"Event '{desc}' added for {event['start_utc']}",
        }

    def _create_note(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.memory:
            return {"status": "error", "message": "Memory store not available"}
        desc = params.get("description", "")
        if not desc:
            return {"status": "error", "message": "Note content required"}
        self.memory.set_state("note", desc)
        return {"status": "success", "message": f"Note saved: {desc[:100]}"}

    def _create_schedule(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.scheduler:
            return {"status": "error", "message": "Scheduler not available"}
        desc = params.get("description", "")
        if not desc:
            return {"status": "error", "message": "Schedule description required"}
        # Try to parse recurrence from description
        recurrence = "daily"
        command = desc
        job = self.scheduler.add(command, recurrence)
        return {
            "status": "success",
            "job_id": job["id"],
            "message": f"Scheduled '{command}' ({recurrence}). id={job['id']}",
        }

    def _list_all(self, params: dict[str, Any]) -> dict[str, Any]:
        results = {}
        if self.task_engine:
            tasks = self.task_engine.list(limit=10)
            results["tasks"] = [{"id": t.id, "command": t.command, "status": t.status} for t in tasks]
        if self.scheduler:
            jobs = self.scheduler.list_jobs()
            results["schedules"] = jobs
        if self.calendar:
            events = self.calendar.upcoming_events(hours=24, limit=10)
            results["upcoming_events"] = events
        return {"status": "success", "results": results}

    def _list_events(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.calendar:
            return {"status": "error", "message": "Calendar not available"}
        events = self.calendar.upcoming_events(hours=168, limit=20)  # 1 week
        return {"status": "success", "events": events}

    def _list_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.task_engine:
            return {"status": "error", "message": "Task engine not available"}
        tasks = self.task_engine.list(limit=20)
        return {
            "status": "success",
            "tasks": [{"id": t.id, "command": t.command, "status": t.status} for t in tasks],
        }

    def _query_time(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "success", "current_time": datetime.now(UTC).isoformat()}

    def _search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("description", "")
        if not self.memory:
            return {"status": "error", "message": "Memory store not available"}
        try:
            from app.core.episodic import EpisodicMemory
            mem = EpisodicMemory(self.memory.database_path)
            episodes = mem.recall_episodes(query=query, limit=5)
            return {"status": "success", "episodes": episodes}
        except Exception:
            return {"status": "error", "message": "Search not available"}


def register_task_planner_tools(
    registry: ToolRegistry,
    settings: Settings,
    task_engine: Any | None = None,
    scheduler: Scheduler | None = None,
    calendar: CalendarStore | None = None,
    memory: MemoryStore | None = None,
) -> None:
    """Register TaskPlanner tools with the tool registry."""
    planner = TaskPlanner(settings, task_engine, scheduler, calendar, memory)

    def parse_and_execute(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text", "")).strip()
        if not text:
            raise ValueError("Missing 'text' parameter for natural language input.")
        confirm = bool(args.get("confirm", False))
        intent = planner.parse(text)
        return planner.execute(intent, confirm=confirm)

    def plan_status(args: dict[str, Any]) -> dict[str, Any]:
        """Get overall productivity status."""
        result = {}
        if task_engine:
            tasks = task_engine.list(limit=5)
            result["pending_tasks"] = len([t for t in tasks if t.status in {"pending", "running"}])
        if scheduler:
            jobs = scheduler.list_jobs()
            result["active_schedules"] = len([j for j in jobs if j.get("active", True)])
        if calendar:
            events = calendar.upcoming_events(hours=24, limit=10)
            result["upcoming_events_24h"] = len(events)
        return {"status": "success", "productivity": result}

    registry.register(
        name="plan",
        description="Natural language productivity planner: create tasks, reminders, events, notes, schedules.",
        risk="low",
        parameters={"text": "string", "confirm": "bool (optional)"},
        handler=parse_and_execute,
    )
    registry.register(
        name="plan_status",
        description="Get overall productivity dashboard status.",
        risk="low",
        parameters={},
        handler=plan_status,
    )
