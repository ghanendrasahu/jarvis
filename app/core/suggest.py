from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.events import EventHub
from app.core.logging import get_logger
from app.core.memory import MemoryStore

logger = get_logger("suggestion_engine")

_SUGGESTION_RULES: list[tuple[tuple[str, ...], str]] = [
    (("screenshot", "look at the screen", "analyze screen", "read the screen"), "analyze the screen"),
    (("recall", "remember", "remember_note", "what do i know"), "remember <note>: add a note to my knowledge base"),
    (("fix", "bug", "debug", "refactor", "failing test"), "run_tests via 'run the test suite' to confirm green"),
    (("schedule", "scheduled", "list schedules"), "list schedules"),
    (("email", "send email"), "upcoming events"),
    (("remind", "reminder"), "list schedules"),
    (("calendar", "event", "upcoming events"), "upcoming events"),
    (("search web", "search the web", "google"), "search web <topic>"),
    (("status", "health", "system status"), "system status"),
    (("suggest", "what can", "what should"), "system status"),
    (("ocr", "read text"), "look at the screen"),
    (("clipboard", "copy", "paste"), "whats in my clipboard"),
]

_FALLBACKS = (
    "system status",
    "what time is it",
    "look at the screen",
    "list processes",
)

_SUGGESTION_WEIGHTS: dict[str, float] = {
    "system_status": 1.0,
    "job_application": 0.9,
    "reminder_due": 0.85,
    "git_state": 0.7,
    "recent_failure": 0.6,
}


class SuggestionEvent:
    """Represents a suggestion event with context and metadata."""
    def __init__(
        self,
        suggestion: str,
        source: str,
        priority: float = 0.5,
        cooldown_until: float | None = None,
        suppressed: bool = False,
    ) -> None:
        self.suggestion = suggestion
        self.source = source
        self.priority = priority
        self.cooldown_until = cooldown_until or 0.0
        self.suppressed = suppressed
        self.timestamp = datetime.now().isoformat()

    def is_expired(self) -> bool:
        return datetime.now().timestamp() >= self.cooldown_until if self.cooldown_until else False

    def __lt__(self, other: SuggestionEvent) -> bool:
        return self.priority > other.priority  # Higher priority first


def _matches(command: str, keywords: tuple[str, ...]) -> bool:
    lowered = (command or "").lower()
    return any(keyword in lowered for keyword in keywords)


class SuggestionEngine:
    """Context-aware proactive suggestion engine."""

    def __init__(
        self,
        event_hub: EventHub | None = None,
        memory: MemoryStore | None = None,
        quiet_hours: int | None = 300,
    ) -> None:
        self.event_hub = event_hub
        self.memory = memory
        self.quiet_hours = quiet_hours  # 5 minutes default
        self.suppressed: set[str] = set()
        self._last_suggestion_time: float = 0.0
        self._suggestion_history: list[SuggestionEvent] = []

    def _in_quiet_hours(self) -> bool:
        if self.quiet_hours is None or self.quiet_hours <= 0:
            return False
        elapsed = datetime.now().timestamp() - self._last_suggestion_time
        return elapsed < self.quiet_hours

    def _score_suggestion(self, suggestion: str, context_score: float = 1.0) -> float:
        """Score a suggestion based on relevance and context.

        Higher score = more relevant.
        """
        base = _SUGGESTION_WEIGHTS.get(suggestion, 0.5)
        # Decay over time since last shown
        age_penalty = max(0.0, (datetime.now().timestamp() - self._last_suggestion_time) / 3600.0) * 0.1
        # Suppressed suggestions get zero priority
        if suggestion in self.suppressed:
            return 0.0
        return max(0.0, base - age_penalty * context_score)

    def _gather_context(self) -> dict[str, Any]:
        """Gather relevant context from memory and system state."""
        context: dict[str, Any] = {}
        if self.memory is not None:
            # Recent job applications
            try:
                jobs = self.memory.recent_job_applications(limit=5)
                if jobs:
                    context["job_applications"] = len(jobs)
                    context["pending_jobs"] = sum(
                        1 for j in jobs if j.get("status", "") not in {"applied", "completed"}
                    )
            except Exception:
                pass
            # Recent reminders
            try:
                reminders = self.memory.get_state("reminders", default=[])
                if reminders:
                    context["reminders"] = len(reminders)
                    # Count due reminders (simplified: any with a when timestamp)
                    due = 0
                    for r in reminders:
                        when = r.get("when")
                        if when:
                            try:
                                from datetime import datetime as _dt
                                if _dt.fromisoformat(when) < datetime.now():
                                    due += 1
                            except Exception:
                                pass
                    context["due_reminders"] = due
            except Exception:
                pass
            # Git state
            try:
                git_status = self.memory.get_state("git_status", default="")
                if git_status:
                    context["git_status"] = git_status
            except Exception:
                pass
        # System-level shortcuts (always available)
        context.setdefault("in_quiet_hours", self._in_quiet_hours())
        return context

    def _generate(self, last_command: str, context: dict[str, Any]) -> list[SuggestionEvent]:
        """Generate suggestion events from rules and context."""
        events: list[SuggestionEvent] = []

        for keywords, suggestion in _SUGGESTION_RULES:
            if _matches(last_command, keywords):
                priority = _SUGGESTION_WEIGHTS.get(suggestion, 0.5)
                if not context.get("in_quiet_hours", False):
                    events.append(SuggestionEvent(
                        suggestion=suggestion,
                        source="rule_match",
                        priority=priority,
                    ))
                break  # Only one rule match per command

        # Add context-aware suggestions
        if context.get("job_applications") and not context.get("in_quiet_hours", False):
            pending = context.get("pending_jobs", 0)
            if pending > 0:
                events.append(SuggestionEvent(
                    suggestion=f"follow up on {pending} pending job application",
                    source="job_application",
                    priority=_SUGGESTION_WEIGHTS.get("job_application", 0.9),
                ))

        if context.get("due_reminders", 0) and not context.get("in_quiet_hours", False):
            events.append(SuggestionEvent(
                suggestion=f"follow up on {context['due_reminders']} due reminder(s)",
                source="reminder_due",
                priority=_SUGGESTION_WEIGHTS.get("reminder_due", 0.85),
            ))

        if context.get("git_status") and not context.get("in_quiet_hours", False):
            events.append(SuggestionEvent(
                suggestion="check git status and recent changes",
                source="git_state",
                priority=_SUGGESTION_WEIGHTS.get("git_state", 0.7),
            ))

        # Add fallbacks if no rule matched
        if not events:
            for fallback in _FALLBACKS:
                events.append(SuggestionEvent(
                    suggestion=fallback,
                    source="fallback",
                    priority=0.3,
                ))

        return events

    def suggest(self, last_command: str | None = None) -> list[str]:
        """Return up to 3 suggestions based on the last command and context.

        Args:
            last_command: The most recent user command, or None if none.

        Returns:
            List of suggestion strings, sorted by priority (highest first),
            suppressed and cooldown-filtered.
        """
        if last_command is None:
            resolved: str = self.memory.get_state("last_command", default="") if self.memory else ""
        else:
            resolved = last_command
            if self.memory is not None:
                self.memory.set_state("last_command", last_command)

        context = self._gather_context()
        events = self._generate(resolved, context)

        # Sort by priority (highest first), then by recency
        events.sort()

        # Filter out expired (cooldown) suggestions
        now = datetime.now().timestamp()
        filtered: list[SuggestionEvent] = []
        for event in events:
            if not event.is_expired():
                filtered.append(event)
                self._last_suggestion_time = now  # track time of first accepted suggestion

        # Dedupe and limit
        seen: list[str] = []
        for event in filtered:
            if event.suggestion not in seen:
                seen.append(event.suggestion)

        result = seen[:3]

        # Publish events
        if self.event_hub is not None and result:
            for event in filtered[:3]:
                self.event_hub.publish("suggestion", {
                    "suggestion": event.suggestion,
                    "source": event.source,
                    "priority": event.priority,
                    "timestamp": event.timestamp,
                })

        return result

    def suppress(self, source: str) -> None:
        """Mark a suggestion source as suppressed by the user."""
        self.suppressed.add(source)
        logger.info("suppressed suggestion source: %s", source)

    def set_quiet_hours(self, seconds: int) -> None:
        """Set the quiet hours cooldown window."""
        self.quiet_hours = seconds
        self._last_suggestion_time = datetime.now().timestamp()

    def remember_last_command(self, command: str) -> None:
        """Remember the last command in memory for rule matching."""
        if self.memory is not None:
            self.memory.set_state("last_command", command)
