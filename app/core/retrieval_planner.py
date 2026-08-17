from __future__ import annotations

from app.core.rag import RagIndex
from app.llm.planner import BasePlanner
from app.models.schemas import Plan


class RetrievalPlanner:
    """Decorates a BasePlanner with knowledge-base retrieval.

    Retrieves the most relevant stored notes/files for the command and forwards
    them to the underlying planner as ``rag_context``. When RAG is disabled or
    the index is empty, calls are passed through untouched (zero added cost).
    """

    def __init__(self, base: BasePlanner, rag: RagIndex | None, top_k: int | None = None) -> None:
        self.base = base
        self.rag = rag
        self.top_k = top_k

    def plan(
        self,
        command: str,
        tool_catalog: list[dict],
        history: list[dict],
        agent_context: str = "",
        tool_history: list[dict] | None = None,
        known_facts: dict | None = None,
        rag_context: str = "",
    ) -> Plan:
        if self.rag is not None:
            rag_context = self.rag.build_context(command, top_k=self.top_k)
        return self.base.plan(
            command,
            tool_catalog,
            history,
            agent_context=agent_context,
            tool_history=tool_history,
            known_facts=known_facts,
            rag_context=rag_context,
        )


def wrap_planner_with_rag(base: BasePlanner, rag: RagIndex | None) -> BasePlanner:
    if rag is None:
        return base
    return RetrievalPlanner(base, rag)
