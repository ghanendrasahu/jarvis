from __future__ import annotations

import ctypes
import time
from datetime import datetime
from typing import Any


class IdleDetector:
    """Tracks how long the user has been idle (no keyboard/mouse input).

    On Windows uses GetLastInputInfo for true system-level idle time; elsewhere
    (or when the API is unavailable) falls back to the last recorded command
    activity timestamp supplied by the caller.
    """

    def __init__(self, memory: Any | None = None) -> None:
        self.memory = memory
        self._last_input_ms: int | None = None
        self._last_input_at: float | None = None
        self._last_input_info: Any
        self._kernel32: Any
        try:
            self._last_input_info = ctypes.windll.user32.GetLastInputInfo
            self._last_input_info.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
            self._last_input_info.restype = ctypes.c_bool
            self._kernel32 = ctypes.windll.kernel32
            self._kernel32.GetTickCount.restype = ctypes.c_ulong
        except Exception:
            self._last_input_info = None
            self._kernel32 = None

    def _windows_idle_ms(self) -> int | None:
        if self._last_input_info is None:
            return None
        try:
            last_input = ctypes.c_ulong()
            if not self._last_input_info(ctypes.byref(last_input)):
                return None
            return int(self._kernel32.GetTickCount()) - int(last_input.value)
        except Exception:
            return None

    def idle_seconds(self) -> int:
        windows_ms = self._windows_idle_ms()
        if windows_ms is not None:
            return max(0, windows_ms // 1000)
        last_at = None
        if self.memory is not None:
            activity = self.memory.get_state("activity", default={}) or {}
            raw = activity.get("last_at")
            if raw:
                try:
                    last_at = datetime.fromisoformat(str(raw))
                    if last_at.tzinfo is not None:
                        last_at = last_at.astimezone()
                except ValueError:
                    last_at = None
        if last_at is None:
            return 0
        return max(0, int(time.time() - last_at.timestamp()))

    def is_idle(self, threshold_seconds: int) -> bool:
        return self.idle_seconds() >= max(0, int(threshold_seconds))
