from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.safe_paths import resolve_safe_path
from app.core.utils import get_pyautogui
from app.core.vision import (
    DEFAULT_DESCRIBE_PROMPT,
    DEFAULT_OCR_PROMPT,
    VisionAnalyzer,
)
from app.tools.registry import ToolRegistry


def register_vision_tools(
    registry: ToolRegistry,
    settings: Settings,
    analyzer: VisionAnalyzer | None = None,
) -> None:
    analyzer = analyzer or VisionAnalyzer(settings)

    def _capture_screen(output_path: str) -> str:
        resolved = resolve_safe_path(output_path, settings=settings, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        image = get_pyautogui().screenshot()
        image.save(str(resolved))
        return str(resolved)

    def analyze_image(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing image path.")
        prompt = str(args.get("prompt", "")).strip() or DEFAULT_DESCRIBE_PROMPT
        safe = resolve_safe_path(path, settings=settings, must_exist=True)
        analysis = analyzer.analyze(str(safe), prompt=prompt)
        return {"path": str(safe), "analysis": analysis, "result_text": analysis[:300]}

    def analyze_screen(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt", "")).strip() or DEFAULT_DESCRIBE_PROMPT
        output_path = str(args.get("output_path", "data/vision_latest.png")).strip()
        captured = _capture_screen(output_path)
        analysis = analyzer.analyze(captured, prompt=prompt)
        return {"path": captured, "analysis": analysis, "result_text": analysis[:300]}

    def ocr_screen(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt", "")).strip() or DEFAULT_OCR_PROMPT
        output_path = str(args.get("output_path", "data/vision_latest.png")).strip()
        captured = _capture_screen(output_path)
        text = analyzer.analyze(captured, prompt=prompt)
        return {"path": captured, "text": text, "result_text": text[:300]}

    def ocr_image(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing image path.")
        prompt = str(args.get("prompt", "")).strip() or DEFAULT_OCR_PROMPT
        safe = resolve_safe_path(path, settings=settings, must_exist=True)
        text = analyzer.analyze(str(safe), prompt=prompt)
        return {"path": str(safe), "text": text, "result_text": text[:300]}

    def detect_window(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing image path.")
        safe = resolve_safe_path(path, settings=settings, must_exist=True)
        result = analyzer.detect_window(str(safe))
        return {"path": str(safe), "window": result, "result_text": str(result)[:300]}

    def detect_errors(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing image path.")
        safe = resolve_safe_path(path, settings=settings, must_exist=True)
        result = analyzer.detect_errors(str(safe))
        return {"path": str(safe), "errors": result, "result_text": str(result)[:300]}

    def analyze_with_context(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing image path.")
        prompt = str(args.get("prompt", "")).strip() or DEFAULT_DESCRIBE_PROMPT
        safe = resolve_safe_path(path, settings=settings, must_exist=True)
        result = analyzer.analyze_with_context(str(safe), prompt=prompt)
        return {"path": str(safe), **result}

    def analyze_screen_with_context(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt", "")).strip() or DEFAULT_DESCRIBE_PROMPT
        output_path = str(args.get("output_path", "data/vision_latest.png")).strip()
        captured = _capture_screen(output_path)
        result = analyzer.analyze_with_context(captured, prompt=prompt)
        return {"path": captured, **result}

    registry.register(
        name="analyze_image",
        description="Describe or analyze the contents of an image file (including any text in it).",
        risk="medium",
        parameters={"path": "string", "prompt": "string"},
        handler=analyze_image,
    )
    registry.register(
        name="analyze_screen",
        description="Capture the screen and describe what is currently on it.",
        risk="medium",
        parameters={"prompt": "string", "output_path": "string"},
        handler=analyze_screen,
    )
    registry.register(
        name="ocr_screen",
        description="Capture the screen and extract all visible text from it.",
        risk="medium",
        parameters={"prompt": "string", "output_path": "string"},
        handler=ocr_screen,
    )
    registry.register(
        name="ocr_image",
        description="Extract all visible text from an image file.",
        risk="medium",
        parameters={"path": "string", "prompt": "string"},
        handler=ocr_image,
    )
    registry.register(
        name="detect_window",
        description="Detect the active application/window in a screenshot.",
        risk="medium",
        parameters={"path": "string"},
        handler=detect_window,
    )
    registry.register(
        name="detect_errors",
        description="Detect error messages and failure indicators in a screenshot.",
        risk="medium",
        parameters={"path": "string"},
        handler=detect_errors,
    )
    registry.register(
        name="analyze_with_context",
        description="Full vision analysis: describe + window detection + error detection + reasoning.",
        risk="medium",
        parameters={"path": "string", "prompt": "string"},
        handler=analyze_with_context,
    )
    registry.register(
        name="analyze_screen_with_context",
        description="Capture screen and run full vision analysis pipeline.",
        risk="medium",
        parameters={"prompt": "string", "output_path": "string"},
        handler=analyze_screen_with_context,
    )
