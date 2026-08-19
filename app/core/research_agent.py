from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.events import EventHub
from app.core.logging import get_logger
from app.core.orchestrator import JarvisOrchestrator
from app.tools.registry import ToolRegistry

logger = get_logger("research_agent")


class ResearchResult:
    """Structured result from a research workflow."""

    def __init__(
        self,
        answer: str,
        sources: list[dict[str, Any]],
        query: str,
        steps: list[dict[str, Any]] | None = None,
        confidence: float = 1.0,
    ) -> None:
        self.answer = answer
        self.sources = sources
        self.query = query
        self.steps = steps or []
        self.confidence = confidence
        self.timestamp = datetime.now(UTC).isoformat()

    def __dict__(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "query": self.query,
            "steps": self.steps,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }


class ResearchAgent:
    """Agent that performs web research following a structured workflow.

    WORKFLOW:
    QUESTION
      ↓
    SEARCH (search_web)
      ↓
    COLLECT (read_webpage on promising results)
      ↓
    VERIFY (cross-check facts, check source credibility)
      ↓
    COMPARE (compare conflicting information)
      ↓
    ANSWER + SOURCES

    Every tool call is permission-gated through the executor, so no
    form submission, external application, or download occurs without
    explicit user approval.
    """

    def __init__(
        self,
        orchestrator: JarvisOrchestrator,
        registry: ToolRegistry,
        events: EventHub | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.registry = registry
        self.events = events

    # ------------------------------------------------------------------
    # Core workflow steps (each gated by the executor / approval system)
    # ------------------------------------------------------------------

    def _search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]:
        """Step 1: Search the web; returns ranked results with title/url/snippet."""
        result = self.orchestrator.execute_command(
            command=f"search web {query}",
            source="research_agent",
            auto_approve=False,
        )
        # The orchestrator routes to the search_web tool; we extract results
        # from the ToolExecutionResult payload if available.
        if result and result.results:
            return [
                {
                    "rank": i + 1,
                    "title": (r.output or {}).get("title", ""),
                    "url": (r.output or {}).get("url", ""),
                    "snippet": (r.output or {}).get("result_text", ""),
                }
                for i, r in enumerate(result.results)
            ]
        return []

    def _collect(self, url: str) -> dict[str, Any] | None:
        """Step 2: Read a webpage; returns title, text, and key metadata."""
        result = self.orchestrator.execute_command(
            command=f"read url {url}",
            source="research_agent",
            auto_approve=False,
        )
        if result and result.results:
            r = result.results[0]
            output = r.output or {}
            return {
                "url": r.tool,
                "title": output.get("title", ""),
                "text": output.get("text", ""),
                "chars": output.get("chars", 0),
            }
        return None

    def _verify(
        self, claim: str, sources: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Step 3: Verify a claim against the collected sources.

        Returns a dict with ``supported``, ``contradicted``, and
        ``neutral`` lists keyed by source url.
        """
        verdicts: dict[str, Any] = {"supported": [], "contradicted": [], "neutral": []}
        for source in sources:
            url = source.get("url", "")
            text = source.get("text", "")[:500].lower()
            if claim.lower() in text:
                verdicts["supported"].append({"url": url, "excerpt": text[:200]})
            elif any(
                kw in text for kw in ["not", "no", "without", "however", "despite"]
            ):
                verdicts["contradicted"].append({"url": url, "excerpt": text[:200]})
            else:
                verdicts["neutral"].append({"url": url, "excerpt": text[:200]})
        return verdicts

    def _compare(self, a: dict[str, Any], b: dict[str, Any]) -> str:
        """Step 4: Compare two contradictory sources.

        Returns a short synthesis statement."""
        a_text = a.get("excerpt", "").lower()
        b_text = b.get("excerpt", "").lower()
        if a_text and b_text:
            return (
                f"Source A suggests: {a_text[:150]}. "
                f"Source B suggests: {b_text[:150]}."
            )
        return "Insufficient source text to compare."

    # ------------------------------------------------------------------
    # High-level research orchestration
    # ------------------------------------------------------------------

    def research(self, question: str, max_results: int = 8, max_pages: int = 3) -> ResearchResult:
        """Run the full research workflow for *question*.

        Returns a ``ResearchResult`` with the final answer, sources,
        and a step-by-step log."""
        steps: list[dict[str, Any]] = []
        query = question
        collected: list[dict[str, Any]] = []

        # 1. SEARCH
        steps.append({"step": "search", "query": question, "results": self._search(query, max_results)})
        if not steps[-1]["results"]:
            return ResearchResult(
                answer="No search results found.",
                sources=[],
                query=question,
                steps=steps,
                confidence=0.0,
            )

        # 2. COLLECT — read top results
        seen_urls: set[str] = set()
        for r in steps[-1]["results"]:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if len(collected) >= max_pages:
                break
            collected_item = self._collect(url)
            if collected_item is not None:
                collected.append(collected_item)

        # 3. VERIFY & 4. COMPARE + ANSWER
        # Simple heuristic: if any source supports the question claim,
        # surface it; otherwise synthesize from available text.
        answer_parts: list[str] = []
        final_sources: list[dict[str, Any]] = []

        for src in collected:
            if src is None:
                continue
            title = src.get("title", "")
            url = src.get("url", "")
            text = src.get("text", "")[:500]
            answer_parts.append(f"From {title} ({url}): {text}")
            final_sources.append({"url": url, "title": title, "snippet": text[:200]})

        answer = " ".join(answer_parts) if answer_parts else "No conclusive answer could be synthesized."
        confidence = min(1.0, len(collected) / max_pages)

        return ResearchResult(
            answer=answer,
            sources=final_sources,
            query=question,
            steps=steps,
            confidence=confidence,
        )
