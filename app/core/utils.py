from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def build_startup_greeting(now: datetime | None = None) -> str:
    """Return the classic Jarvis startup greeting for the time of day."""
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        part = "morning"
    elif 12 <= hour < 18:
        part = "afternoon"
    else:
        part = "evening"
    return f"Good {part}, sir. All systems are online."


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_duration_to_seconds(raw_value: str) -> int:
    """Parse a timestamp like ``1:25``, ``1:02:03``, ``2m 30s`` or ``90`` into seconds."""
    text = raw_value.strip().lower()
    if not text:
        raise ValueError("Missing timestamp.")

    match = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", text)
    if match:
        first = int(match.group(1))
        second = int(match.group(2))
        third = match.group(3)
        if third is None:
            return (first * 60) + second
        return (first * 3600) + (second * 60) + int(third)

    unit_pattern = re.compile(
        r"(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*"
        r"(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
        r"(?:(?P<seconds>\d+)\s*s(?:ec(?:ond)?s?)?)?$"
    )
    unit_match = unit_pattern.fullmatch(text)
    if unit_match and any(unit_match.groupdict().values()):
        hours = int(unit_match.group("hours") or 0)
        minutes = int(unit_match.group("minutes") or 0)
        seconds = int(unit_match.group("seconds") or 0)
        return (hours * 3600) + (minutes * 60) + seconds

    if text.isdigit():
        return int(text)

    raise ValueError(f"Unsupported timestamp format: '{raw_value}'")


def get_pyautogui():
    """Return a configured pyautogui module (FASTSAFE enabled)."""
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    return pyautogui
