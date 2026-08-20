from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.events import EventHub
from app.core.logging import get_logger
from app.core.research_agent import ResearchAgent
from app.tools.registry import ToolRegistry

logger = get_logger("research_tools")


def register_research_tools(
    registry: ToolRegistry,
    settings: Settings,
    orchestrator: Any | None = None,
    events: EventHub | None = None,
) -> None:
    """Register the research agent workflow as a callable tool.

    The router emits ``research_agent_start`` for research intents; the handler
    runs the deterministic search -> collect -> verify -> answer workflow and
    returns the answer with cited sources. No form submission or external
    side effect happens without approval.
    """

    def research_agent_start(args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question") or "").strip()
        if not question:
            return {
                "result_text": "No research question provided.",
                "error": "Missing 'question' argument.",
            }
        if orchestrator is None:
            return {
                "result_text": "Research agent is unavailable in this runtime.",
                "error": "Research agent not wired (no orchestrator).",
            }
        agent = ResearchAgent(orchestrator=orchestrator, registry=registry, events=events)
        result = agent.research(
            question,
            max_results=max(1, min(10, int(args.get("max_results", 8)))),
            max_pages=max(1, min(5, int(args.get("max_pages", 3)))),
        )
        payload = result.to_dict()
        payload["result_text"] = result.answer
        return payload

    registry.register(
        name="research_agent_start",
        description=(
            "Run the structured web research workflow: search, read the most promising "
            "pages, verify claims across sources, and return an answer with citations. "
            "Use for research requests like 'research <topic>', 'find reliable "
            "information about <topic>', or 'compare <A> and <B>'."
        ),
        risk="low",
        parameters={"question": "string", "max_results": "integer", "max_pages": "integer"},
        handler=research_agent_start,
    )
