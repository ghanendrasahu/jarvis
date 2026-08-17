from __future__ import annotations

import base64

import pytest

from app.config import Settings
from app.core.vision import VisionAnalyzer, detect_image_format
from app.tools.registry import ToolRegistry
from app.tools.vision_tools import register_vision_tools

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _png(tmp_path) -> str:
    path = tmp_path / "sample.png"
    path.write_bytes(_PNG_1PX)
    return str(path)


def test_detect_image_format(tmp_path):
    assert detect_image_format(__import__("pathlib").Path(_png(tmp_path))) == "png"


def test_provider_resolution_auto_mirrors_llm():
    settings = Settings(llm_provider="groq", groq_api_key="k")
    assert VisionAnalyzer(settings).provider == "groq"
    settings = Settings(llm_provider="openai", openai_api_key="k")
    assert VisionAnalyzer(settings).provider == "openai"


def test_provider_availability():
    disabled = Settings(vision_provider="disabled")
    ok, reason = VisionAnalyzer(disabled).available()
    assert not ok and "disabled" in reason

    no_key = Settings(vision_provider="groq", groq_api_key=None)
    ok, _ = VisionAnalyzer(no_key).available()
    assert not ok

    ready = Settings(vision_provider="ollama", ollama_base_url="http://localhost:11434")
    ok, _ = VisionAnalyzer(ready).available()
    assert ok


def test_openai_compatible_payload(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "I see a terminal window."}}]}

    monkeypatch.setattr(
        "app.core.vision.requests.post",
        lambda endpoint, headers=None, json=None, timeout=None: (captured.update(endpoint=endpoint, json=json) or FakeResponse()),
    )
    analyzer = VisionAnalyzer(Settings(llm_provider="groq", groq_api_key="secret", vision_provider="auto"))
    text = analyzer.analyze(_png(tmp_path), prompt="what do you see?")
    assert text == "I see a terminal window."
    assert "api.groq.com" in captured["endpoint"]
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["text"] == "what do you see?"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["json"]["model"]


def test_ollama_payload(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "desk with monitor"}}

    monkeypatch.setattr(
        "app.core.vision.requests.post",
        lambda url, json=None, timeout=None: (captured.update(url=url, json=json) or FakeResponse()),
    )
    analyzer = VisionAnalyzer(Settings(vision_provider="ollama"))
    text = analyzer.analyze(_png(tmp_path))
    assert text == "desk with monitor"
    assert captured["url"].endswith("/api/chat")
    message = captured["json"]["messages"][0]
    assert message["images"][0] == base64.b64encode(_PNG_1PX).decode("ascii")
    assert captured["json"]["model"] == "llava"


def test_analyze_rejects_non_image(tmp_path):
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not an image", encoding="utf-8")
    analyzer = VisionAnalyzer(Settings(vision_provider="ollama"))
    with pytest.raises(ValueError, match="Not a supported image"):
        analyzer.analyze(str(text_file))


class StubAnalyzer:
    def analyze(self, image_path: str, prompt: str = "") -> str:
        return f"stub analysis of {image_path.split(chr(92))[-1]}"


def _registry(tmp_path):
    settings = Settings(workspace_root=str(tmp_path), data_dir=str(tmp_path / "data"))
    registry = ToolRegistry()
    register_vision_tools(registry, settings, analyzer=StubAnalyzer())  # type: ignore[arg-type]
    return registry


def test_analyze_image_tool(tmp_path):
    registry = _registry(tmp_path)
    out = registry.execute("analyze_image", {"path": _png(tmp_path), "prompt": "what is it?"})
    assert "sample.png" in out["analysis"]
    assert out["result_text"]


def test_ocr_image_tool(tmp_path):
    registry = _registry(tmp_path)
    out = registry.execute("ocr_image", {"path": _png(tmp_path)})
    assert "sample.png" in out["text"]


def test_analyze_image_requires_path(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(ValueError, match="Missing image path"):
        registry.execute("analyze_image", {})
