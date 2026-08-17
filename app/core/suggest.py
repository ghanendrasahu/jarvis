from __future__ import annotations

from collections.abc import Sequence

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


def _matches(command: str, keywords: tuple[str, ...]) -> bool:
    lowered = (command or "").lower()
    return any(keyword in lowered for keyword in keywords)


class SuggestionEngine:
    """Rule-based intent prediction: suggests follow-up commands for common workflows."""

    def suggest(self, last_command: str, limit: int = 3) -> list[str]:
        limit = max(1, min(5, int(limit)))
        for keywords, suggestion in _SUGGESTION_RULES:
            if _matches(last_command, keywords):
                return self._dedupe([suggestion] + list(_FALLBACKS), limit)
        return list(_FALLBACKS)[:limit]

    @staticmethod
    def _dedupe(items: Sequence[str], limit: int) -> list[str]:
        seen: list[str] = []
        for item in items:
            cleaned = item.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen[:limit]
