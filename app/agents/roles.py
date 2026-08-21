from __future__ import annotations

from dataclasses import dataclass
from typing import Any

"""Unified agent roles and intent-based selection (Phase C).

Specialized agents are *configurations* of the shared autonomous loop
(role prompt + intent vocabulary + preferred tools), not separate classes.
The orchestrator picks a role with ``classify_intent`` and hands it to the
shared ``AutonomousAgent`` (or ``CodeAgent`` for the coding role). This keeps
one executor primitive, one registry, one permission model, and one memory.
"""


@dataclass(frozen=True)
class AgentRole:
    id: str
    name: str
    description: str
    intents: tuple[str, ...]
    role_prompt: str
    priority: int = 0  # higher wins ties


def _role_prompt(name: str, instructions: str) -> str:
    return f"{name} MODE:\n{instructions}"


CODING_ROLE = _role_prompt(
    "CODE",
    """You are a coding agent working directly in the user's repository.
Workflow:
1. Understand the task with search_code and read_text_file before changing anything.
2. Make minimal, surgical changes with edit_file (or write_text_file for new files).
3. After editing, ALWAYS finish by running lint_code.
4. If lint fails, fix the code and rerun until everything passes.
Use only tools listed in the catalog. When verification passes, emit agent_done with a summary of what changed.""",
)

KNOWLEDGE_ROLE = _role_prompt(
    "KNOWLEDGE",
    """You manage the user's knowledge base.
Use recall to find relevant stored notes, remember_note to save new facts,
index_file/index_notes to ingest files, and knowledge_stats for an overview.
Answer from retrieved knowledge and cite which note/file it came from. If nothing
relevant is stored, say so instead of inventing facts. When you save a new note,
emit agent_done with a confirmation.""",
)

RESEARCH_ROLE = _role_prompt(
    "RESEARCH",
    """You are a research agent.
Workflow: search_web to find sources, read_webpage to read the most promising ones,
compare the information across sources, and answer with the sources cited.
Prefer authoritative sources. If a page can't be read, note that clearly rather than
guessing. Do NOT open forms or submit anything. When done, emit agent_done with the
answer and the sources you used.""",
)

SYSTEM_ROLE = _role_prompt(
    "SYSTEM DIAGNOSTIC",
    """You diagnose the user's computer.
Use system_status, list_processes, and get_current_time to gather facts, then reason
about the most likely cause of the reported problem. Explain the diagnosis in plain
language and recommend the safest next action. Never kill processes or run shell
commands without approval; those are permission-gated and will pause for the user.
You can also use diagnose_system to get a full diagnosis including GPU status,
correlated process analysis, and suggested actions with approval gates.""",
)

VISION_ROLE = _role_prompt(
    "VISION",
    """You understand the user's screen and images.
Use capture_screenshot + analyze_screen to describe what is on screen, ocr_screen to
read visible text, and analyze_image/ocr_image for image files. Read any visible error
text carefully and connect it to what the user asked. Never click, type, or move the
mouse to interact with uncertain targets.""",
)

PRODUCTIVITY_ROLE = _role_prompt(
    "PRODUCTIVITY",
    """You manage the user's tasks, reminders, calendar, and notes.
Use upcoming_events/list_events for calendar, set_reminder for reminders,
remember_note/recall for notes, and schedule/list_schedules for recurring commands.
Plan the user's day from the real data you retrieve. Never send email or change
external services without explicit user confirmation.""",
)

BROWSER_ROLE = _role_prompt(
    "BROWSER",
    """You work in the browser on the user's behalf.
Use open_url/search_web/read_webpage to find and read information, and browser_action
for targeted navigation. Never submit forms, log in, or send messages without explicit
approval. Summarize what you found and cite the page.""",
)

MONITORING_ROLE = _role_prompt(
    "MONITORING",
    """You monitor the user's system and activity.
Use system_status, list_processes, idle_time, and get_runtime_metrics to check for
issues and summarize the current state. Flag abnormal CPU/RAM/disk usage and failed
scheduled runs. Suggest (do not perform) follow-up actions.""",
)

GENERAL_ROLE = _role_prompt(
    "GENERAL",
    """You are JARVIS acting on a multi-step goal. Break the goal into tool actions,
observe results, and keep going until the goal is complete or you hit a blocker.
Prefer the fewest, highest-impact actions. When complete, emit agent_done with a
summary of what was accomplished.""",
)

AGENT_ROLES: tuple[AgentRole, ...] = (
    AgentRole("coding", "CodingAgent", "Edit and verify code in the repository", (), CODING_ROLE, priority=100),
    AgentRole(
        "knowledge",
        "KnowledgeAgent",
        "Store, search, and answer from the personal knowledge base",
        (
            "remember",
            "recall",
            "what do i know",
            "what do you know about",
            "search my notes",
            "notes about",
            "what s in my notes",
            "what's in my notes",
            "knowledge base",
            "save a note",
            "index my",
        ),
        KNOWLEDGE_ROLE,
        priority=90,
    ),
    AgentRole(
        "research",
        "ResearchAgent",
        "Search the web, read sources, and answer with citations",
        (
            "research",
            "investigate",
            "find out",
            "look up",
            "search the web",
            "search web for",
            "search the internet",
            "latest",
            "compare",
            "tell me about",
            "explain why",
            "when was",
            "who is",
            "is there a",
            "how much",
            "what is the price",
            "news about",
        ),
        RESEARCH_ROLE,
        priority=80,
    ),
    AgentRole(
        "vision",
        "VisionAgent",
        "Understand screens and images (describe, OCR, UI context)",
        (
            "what s on my screen",
            "what's on my screen",
            "what is on my screen",
            "look at the screen",
            "look at screen",
            "look at this image",
            "analyze the screen",
            "analyze screen",
            "analyze image",
            "read the screen",
            "read screen",
            "ocr",
            "describe the image",
            "why isn t this working",
            "why isn't this working",
            "why is this not working",
            "why doesnt this work",
        ),
        VISION_ROLE,
        priority=70,
    ),
    AgentRole(
        "system",
        "SystemAgent",
        "Diagnose CPU/RAM/disk/network/battery problems",
        (
            "why is my",
            "why is it",
            "diagnose",
            "troubleshoot",
            "high cpu",
            "high ram",
            "high memory",
            "memory leak",
            "disk space",
            "low on disk",
            "network issue",
            "slow down",
            "computer is slow",
            "laptop is slow",
            "pc is slow",
            "battery",
            "check system",
            "system check",
        ),
        SYSTEM_ROLE,
        priority=60,
    ),
    AgentRole(
        "productivity",
        "ProductivityAgent",
        "Tasks, reminders, calendar, notes, daily planning",
        (
            "plan my day",
            "daily plan",
            "what s important today",
            "what's important today",
            "what am i forgetting",
            "remind me",
            "add event",
            "upcoming events",
            "send email",
            "write email",
            "schedule a",
            "schedule the",
            "my tasks",
            "todo",
            "to do",
            "set a timer",
            "calendar",
        ),
        PRODUCTIVITY_ROLE,
        priority=50,
    ),
    AgentRole(
        "browser",
        "BrowserAgent",
        "Agentic browsing: navigate, read pages, find information",
        (
            "browse",
            "navigate to",
            "visit website",
            "visit the website",
            "open website",
            "check website",
            "read webpage",
            "read this page",
            "on the web",
        ),
        BROWSER_ROLE,
        priority=40,
    ),
    AgentRole(
        "monitoring",
        "MonitoringAgent",
        "Monitor system health, scheduled runs, and activity",
        (
            "monitor",
            "keep an eye",
            "watch for",
            "alert me",
            "notify me when",
            "track ",
            "check on",
        ),
        MONITORING_ROLE,
        priority=30,
    ),
)

# Deterministic prefixes that warrant the autonomous agent even when no
# specific role matches (preserves the previous agent-worthiness behavior).
GENERAL_INTENT_PREFIXES = (
    "research",
    "investigate",
    "figure out",
    "find out why",
    "troubleshoot",
    "diagnose",
    "set up a",
    "set up an",
    "set up the",
    "automate",
    "build me",
    "create a script",
    "scan",
    "audit",
    "monitor",
    "summarize",
    "analyze",
    "check if",
)

# Kept from the legacy prefix router so code routing stays byte-identical.
CODE_INTENT_PREFIXES = (
    "fix",
    "bug",
    "debug",
    "refactor",
    "implement",
    "write a",
    "write an",
    "write the",
    "add a",
    "add an",
    "add the",
    "add tests",
    "add test",
    "create a function",
    "create a class",
    "create a script",
    "optimize the code",
    "clean up the code",
    "make the code",
    "make the script",
    "build a python",
    "code",
)


def is_code_command(command: str) -> bool:
    lowered = (command or "").lower().strip()
    if lowered.startswith(CODE_INTENT_PREFIXES):
        return True
    return any(token in lowered for token in ("fix the bug", "fix a bug", "failing test", "red test", "lint error"))


def is_general_agent_command(command: str) -> bool:
    lowered = (command or "").lower().strip()
    return lowered.startswith(GENERAL_INTENT_PREFIXES)


def classify_intent(command: str, context: dict[str, Any] | None = None) -> AgentRole | None:
    """Return the best-matching agent role for a command, or None.

    Keyword scoring over the role intents. Context (e.g. active surface) can
    suppress classification when the command is clearly a follow-up.
    """
    text = (command or "").lower().strip()
    if not text:
        return None

    surface = str((context or {}).get("active_surface") or "").strip().lower()
    if surface in {"youtube", "whatsapp"}:
        return None

    scores: list[tuple[AgentRole, int]] = []
    for role in AGENT_ROLES:
        if role.id == "coding":
            continue  # handled separately via is_code_command
        count = sum(1 for intent in role.intents if intent in text)
        if count:
            scores.append((role, count))
    if not scores:
        return None
    best_role, best_score = scores[0]
    for role, score in scores[1:]:
        if score > best_score or (score == best_score and role.priority > best_role.priority):
            best_role, best_score = role, score
    return best_role


def role_for_id(role_id: str) -> AgentRole | None:
    for role in AGENT_ROLES:
        if role.id == role_id:
            return role
    return None
