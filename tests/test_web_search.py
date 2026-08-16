from __future__ import annotations

from app.config import Settings
from app.tools import browser_tools
from app.tools.browser_tools import parse_ddg_results
from app.tools.registry import ToolRegistry

_SAMPLE_PAGE = """<!DOCTYPE html><html><body>
<div id="links">
<div class="result results_links results_links_deep web-result ">
    <h2 class="result__title"><a rel="nofollow" class="result__a"
        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FPython_%28programming_language%29&amp;rut=abc123">Python (programming language) - Wikipedia</a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=...">Python is a high-level, general-purpose programming language. Its design philosophy emphasizes code readability.</a>
</div>
<div class="result results_links results_links_deep web-result ">
    <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://www.python.org/">Welcome to Python.org</a></h2>
    <a class="result__snippet" href="https://www.python.org/">The official home of the Python Programming Language.</a>
</div>
</div></body></html>"""


def _registry(monkeypatch, structured=None) -> ToolRegistry:
    if structured is not None:
        monkeypatch.setattr(browser_tools, "search_web_structured", lambda query, max_results=8: structured)
    registry = ToolRegistry()
    browser_tools.register_browser_tools(registry, Settings(workspace_root="."))
    return registry


def test_parse_ddg_results():
    results = parse_ddg_results(_SAMPLE_PAGE, max_results=8)
    assert len(results) == 2
    first = results[0]
    assert first["rank"] == 1
    assert first["title"] == "Python (programming language) - Wikipedia"
    assert first["url"] == "https://en.wikipedia.org/wiki/Python_(programming_language)"
    assert first["snippet"].startswith("Python is a high-level")
    assert first["source"] == "duckduckgo"


def test_parse_ddg_results_respects_max():
    results = parse_ddg_results(_SAMPLE_PAGE, max_results=1)
    assert len(results) == 1


def test_parse_ddg_results_deduplicates():
    doubled = _SAMPLE_PAGE + '<div class="result"><a class="result__a" href="https://www.python.org/">Welcome to Python.org</a></div>'
    results = parse_ddg_results(doubled, max_results=8)
    assert len(results) == 2


def test_search_web_returns_structured_results(monkeypatch):
    registry = _registry(
        monkeypatch,
        structured=[{"rank": 1, "title": "T", "url": "https://x.dev", "snippet": "S", "source": "duckduckgo"}],
    )
    out = registry.execute("search_web", {"query": "hello world", "max_results": 5})
    assert out["engine"] == "duckduckgo"
    assert out["count"] == 1
    assert out["results"][0]["url"] == "https://x.dev"
    assert out["fallback_opened_browser"] is False


def test_search_web_falls_back_to_browser(monkeypatch):
    registry = _registry(monkeypatch, structured=[])
    opened: dict[str, str] = {}
    monkeypatch.setattr(browser_tools.webbrowser, "open", lambda url, new: opened.setdefault("url", url))
    out = registry.execute("search_web", {"query": "nothing here"})
    assert out["count"] == 0
    assert out["fallback_opened_browser"] is True
    assert "google.com/search?q=" in opened["url"]
