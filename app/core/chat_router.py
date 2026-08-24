from __future__ import annotations

import re
from typing import Literal

Route = Literal["chat", "command"]

# Phrases the existing DirectCommandRouter already owns; matching these keeps
# current behaviour intact instead of diverting them into conversation.
_EXACT_COMMANDS = {
    "system status",
    "status",
    "health check",
    "metrics",
    "dashboard",
    "time",
    "current time",
    "date and time",
    "what time is it",
    "what s the time",
    "whats the time",
    "tell me the time",
    "list apps",
    "show apps",
    "app list",
    "list schedules",
    "list shortcuts",
    "job status",
    "job cancel",
    "job resume",
    "job applied",
    "applications",
    "take screenshot",
    "take a screenshot",
    "screenshot",
    "analyze screen",
    "analyze the screen",
    "ocr screen",
    "read the screen",
    "whats on my screen",
    "what s on my screen",
    "what's on my screen",
    "idle time",
    "suggestions",
    "what can i do",
    "what can you do",
    "what should i do next",
    "suggest something",
    "look at the screen",
}

_STRONG_COMMAND_PREFIX = re.compile(
    r"""^(
        open|close|launch|quit|kill|
        play|pause|resume|next|previous|prev|skip|forward|rewind|seek|
        mute|unmute|fullscreen|
        youtube|
        search(\s+web)?(\s+for)?|
        research|investigate|look\s+up|
        run|type|press|announce|
        remind\s+me|
        read\s+(file|url)|
        find\s+files|
        move\s+file|
        apply\s+for|
        whatsapp|
        volume|
        list\s+\w+
    )\b""",
    re.VERBOSE,
)

_URL_RE = re.compile(r"^https?://\S+$")

_QUESTION_MARKER = re.compile(
    r"^\s*(what|who|why|how|when|where|which|whose|is|are|am|do|does|did|can|could|"
    r"should|would|will|shall|tell|explain|describe|define|write|draft|compose|"
    r"summarize|summarise|translate|compare|give|help|teach|show me how|any idea|"
    r"your name|who are you)\b",
    re.IGNORECASE,
)

# Explicit escape hatch forcing the classic command pipeline even when the
# text looks conversational.
_FORCE_COMMAND_PREFIXES = ("/cmd ", "/command ", "jarvis run ", "jarvis command ")


def classify_user_input(text: str) -> Route:
    """Deterministic lightweight routing: ordinary conversation vs command pipeline.

    Pure heuristics - never calls an LLM. Ambiguous input defaults to ``chat``
    so normal conversation never reaches the heavy planner.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return "chat"

    lowered = re.sub(r"\s+", " ", cleaned.lower())

    for prefix in _FORCE_COMMAND_PREFIXES:
        if lowered.startswith(prefix):
            return "command"

    body = lowered
    for prefix in _FORCE_COMMAND_PREFIXES:
        if lowered.startswith(prefix):
            body = lowered[len(prefix) :].strip()
            break

    if _URL_RE.match(body):
        return "command"

    if body in _EXACT_COMMANDS:
        return "command"

    # Time questions are deterministic tool lookups in JARVIS.
    if re.match(r"^(what('?s| is)|tell me)\s+(the\s+)?(current\s+)?time", body):
        return "command"

    if _STRONG_COMMAND_PREFIX.match(body):
        return "command"

    if _QUESTION_MARKER.match(body) or "?" in cleaned:
        return "chat"

    # Short verb-less utterances are treated as conversation.
    return "chat"


def is_conversational(text: str) -> bool:
    return classify_user_input(text) == "chat"
