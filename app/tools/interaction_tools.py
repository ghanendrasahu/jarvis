from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.safe_paths import resolve_safe_path
from app.core.utils import get_pyautogui, safe_float, safe_int
from app.tools.registry import ToolRegistry


def register_interaction_tools(registry: ToolRegistry, settings: Settings) -> None:
    def type_text(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text", ""))
        interval = safe_float(args.get("interval", 0.02), 0.02)
        if not text:
            raise ValueError("Missing text.")
        time.sleep(safe_float(args.get("pre_delay_seconds", 0.3), 0.3))
        pyautogui = get_pyautogui()
        pyautogui.write(text, interval=interval)
        return {"typed_chars": len(text)}

    def press_hotkey(args: dict[str, Any]) -> dict[str, Any]:
        keys = args.get("keys") or []
        if not isinstance(keys, list) or not keys:
            raise ValueError("keys must be a non-empty list.")
        keys = [str(key).strip().lower() for key in keys if str(key).strip()]
        pyautogui = get_pyautogui()
        pyautogui.hotkey(*keys)
        return {"pressed": keys}

    def click_mouse(args: dict[str, Any]) -> dict[str, Any]:
        x = safe_int(args.get("x", 0))
        y = safe_int(args.get("y", 0))
        button = str(args.get("button", "left")).lower()
        clicks = max(1, safe_int(args.get("clicks", 1), 1))
        pyautogui = get_pyautogui()
        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        return {"x": x, "y": y, "button": button, "clicks": clicks}

    def capture_screenshot(args: dict[str, Any]) -> dict[str, Any]:
        path_value = str(args.get("output_path", "data/latest_screenshot.png"))
        resolved = resolve_safe_path(path_value, settings=settings, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        pyautogui = get_pyautogui()
        image = pyautogui.screenshot()
        image.save(str(resolved))
        return {"path": str(resolved), "size_bytes": Path(resolved).stat().st_size}

    def list_windows(_: dict[str, Any]) -> dict[str, Any]:
        import pygetwindow as gw

        titles = [title for title in gw.getAllTitles() if title.strip()]
        return {"count": len(titles), "windows": titles[:200]}

    def focus_window(args: dict[str, Any]) -> dict[str, Any]:
        title_contains = str(args.get("title_contains", "")).strip().lower()
        if not title_contains:
            raise ValueError("Missing title_contains.")
        import pygetwindow as gw

        for window in gw.getAllWindows():
            title = (window.title or "").strip()
            if title_contains in title.lower():
                if window.isMinimized:
                    window.restore()
                window.activate()
                return {"focused": title}
        raise RuntimeError(f"No window found containing: {title_contains}")

    registry.register(
        name="type_text",
        description="Type text into the active application.",
        risk="medium",
        parameters={"text": "string", "interval": "number", "pre_delay_seconds": "number"},
        handler=type_text,
    )
    registry.register(
        name="press_hotkey",
        description="Send keyboard shortcut to active window.",
        risk="medium",
        parameters={"keys": "list[string]"},
        handler=press_hotkey,
    )
    registry.register(
        name="click_mouse",
        description="Click at specific screen coordinates.",
        risk="medium",
        parameters={"x": "integer", "y": "integer", "button": "string", "clicks": "integer"},
        handler=click_mouse,
    )
    registry.register(
        name="capture_screenshot",
        description="Capture the current screen to a PNG file.",
        risk="low",
        parameters={"output_path": "string"},
        handler=capture_screenshot,
    )
    registry.register(
        name="list_windows",
        description="List open desktop window titles.",
        risk="low",
        parameters={},
        handler=list_windows,
    )
    registry.register(
        name="focus_window",
        description="Focus a window by title substring.",
        risk="medium",
        parameters={"title_contains": "string"},
        handler=focus_window,
    )

