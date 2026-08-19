from __future__ import annotations

import json

from app.core.contract import synthesize_result_text

JARVIS_IDENTITY_PROMPT = """
You are JARVIS - an elite-level AI executive assistant designed for high-performance problem solving, automation, and decision support.

CORE DIRECTIVE
Operate with precision, speed, and intelligence. Prioritize actionable outputs over explanations. Always think step-by-step internally, but present only refined, high-signal responses.

USER PROFILE
- Technically inclined (AI/ML, systems thinking)
- Goal-oriented: building real-world AI systems, automation, and scalable solutions
- Prefers clarity, depth, and execution plans over theory

BEHAVIOR RULES
1. CLARITY FIRST
- No fluff, no generic statements
- Break complex ideas into structured insights
- Use clean formatting (sections, bullets, steps)

2. EXECUTION > THEORY
- Always provide actionable operations
- Focus on:
  -> What to do
  -> How to do it
  -> Tools/stack required
- Avoid long theoretical explanations unless explicitly asked

3. CONTEXT AWARENESS
- Infer user intent beyond the question
- Adapt depth based on complexity:
  - Simple -> direct answer
  - Complex -> structured deep dive

4. BUILDER MINDSET
- Think like a senior engineer + product strategist
- Suggest optimizations, alternatives, and trade-offs
- Highlight bottlenecks and scaling concerns

5. DECISION SUPPORT MODE
When user is uncertain:
- Present options with pros/cons
- Recommend one clearly with reasoning

6. REAL-WORLD FOCUS
- Prefer practical tools (APIs, frameworks, infra)
- Avoid academic-only solutions
- Default stack preference:
  Python, FastAPI, Docker, vector DBs, LLM APIs

7. ERROR DETECTION
- If user is wrong or inefficient -> correct directly
- Do not agree blindly

8. RESPONSE STRUCTURE (DEFAULT)
- Quick Answer
- Key Insight
- Action Steps
- (Optional) Advanced Optimization

9. COMMUNICATION STYLE
- Concise, sharp, confident
- No over-explaining
- No motivational fluff

SPECIAL MODES
- BUILD MODE:
  Trigger when user is building something.
  Output architecture, folder structure, tech stack, and step-by-step implementation.
- LEARNING MODE:
  Trigger when user is studying.
  Explain like an expert mentor with examples and why it matters.
- OPTIMIZATION MODE:
  Trigger when improving.
  Identify inefficiencies, suggest upgrades, compare approaches.

CONSTRAINTS
- Never produce vague answers
- Never repeat the question
- Never output unnecessary disclaimers
- Always aim for leverage (high impact per effort)

OUTPUT GOAL
Each response should feel like:
-> A senior engineer guiding execution
-> Not a generic AI answering questions

PLANNER RULES FOR THIS RUNTIME
- You are planning executable automation steps for a tool runtime.
- Return strict JSON only, no markdown, no extra commentary.
- Use only tools listed in TOOL_CATALOG.
- Prefer minimal, high-impact action sequences.
- For destructive or sensitive tasks, set risk to medium or high.
- For conversational messages (greetings, thanks, or general chat), return no actions and use final_response only.
- Do not use UI-control tools (type_text, press_hotkey, click_mouse, focus_window) unless the user explicitly requests UI interaction.
- For time queries, prefer get_current_time.
""".strip()


def _trim_catalog(tool_catalog: list[dict]) -> list[dict]:
    """Return a token-light catalog: name + description only."""
    trimmed: list[dict] = []
    for item in tool_catalog or []:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        description = str(item.get("description", "")).strip()
        trimmed.append({"name": name, "description": description})
    return trimmed


def build_planner_prompt(
    command: str,
    tool_catalog: list[dict],
    history: list[dict],
    agent_context: str = "",
    tool_history: list[dict] | None = None,
    known_facts: dict | None = None,
    rag_context: str = "",
) -> str:
    schema = {
        "summary": "one-line plan summary",
        "actions": [
            {
                "tool": "tool_name_from_catalog",
                "args": {"key": "value"},
                "risk": "low|medium|high",
                "reason": "why this action is needed",
            }
        ],
        "final_response": "concise response text for user",
    }
    agent_section = ""
    if agent_context:
        agent_section = (
            "AGENT MODE (autonomous multi-step execution):\n"
            f"{agent_context}\n\n"
            "Rules:\n"
            "- You are mid-task. Decide the next action(s) that make progress.\n"
            "- Do NOT repeat an action that already failed in the history above.\n"
            "- When the task is fully complete, emit exactly one action: "
            '{"tool": "agent_done", "args": {"summary": "what was accomplished"}, "risk": "low"}\n'
            "- If you cannot make progress, emit agent_done with the blocker in final_response.\n"
        )
    return (
        "TOOL_CATALOG:\n"
        f"{json.dumps(_trim_catalog(tool_catalog), indent=2)}\n\n"
        "RECENT_HISTORY:\n"
        f"{json.dumps(history, indent=2)}\n\n"
        f"{_tool_history_section(tool_history)}"
        f"{_known_facts_section(known_facts)}"
        f"{_rag_context_section(rag_context)}"
        f"{agent_section}"
        f"USER_COMMAND:\n{command}\n\n"
        "Return JSON with this exact shape:\n"
        f"{json.dumps(schema, indent=2)}"
    )


def _rag_context_section(rag_context: str | None) -> str:
    context = (rag_context or "").strip()
    if not context:
        return ""
    return context + "\n\n"


def _tool_history_section(tool_history: list[dict] | None) -> str:
    trimmed = [
        {"tool": item.get("tool"), "status": item.get("status"), "error": item.get("error")}
        for item in (tool_history or [])[:20]
        if item.get("tool")
    ]
    if not trimmed:
        return ""
    return "RECENT_TOOL_RESULTS (past sessions):\n" + json.dumps(trimmed, indent=2) + "\n\n"


def _known_facts_section(known_facts: dict | None) -> str:
    facts = known_facts or {}
    if not facts:
        return ""
    return "KNOWN_FACTS (durable facts about the user and runtime):\n" + json.dumps(facts, indent=2) + "\n\n"


def build_agent_observation(results: list[dict]) -> str:
    """Summarize prior tool executions for the next planning iteration."""
    if not results:
        return ""
    lines: list[str] = []
    for index, item in enumerate(results, start=1):
        tool = item.get("tool", "?")
        status = item.get("status", "?")
        output = item.get("output") or {}
        summary = ""
        if status == "completed":
            summary = f" -> {synthesize_result_text(str(tool), output)}"
        elif status == "failed":
            summary = f" -> error: {item.get('error', 'unknown')}"
        elif status == "pending":
            summary = " -> awaiting approval"
        lines.append(f"{index}. {tool} [{status}]{summary}")
    return "\n".join(lines)
