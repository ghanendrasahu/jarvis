from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312
WM_USER = 0x0400


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class GlobalHotkey:
    """Registers a system-wide hotkey on Windows via RegisterHotKey + a message loop thread."""

    def __init__(self, vk: int = 0x4A, modifiers: int = MOD_CONTROL | MOD_SHIFT, hotkey_id: int = 1) -> None:
        self._vk = vk
        self._modifiers = modifiers
        self._id = hotkey_id
        self._thread: threading.Thread | None = None
        self._callback: Callable[[], None] | None = None

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def start(self, callback: Callable[[], None]) -> bool:
        if not self.supported or self._thread is not None:
            return False
        self._callback = callback
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._thread is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread.ident, WM_USER, 0, 0)
            except Exception:
                pass
            self._thread = None

    def _loop(self) -> None:
        user32 = ctypes.windll.user32
        msg = _MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001)
        try:
            user32.RegisterHotKey(None, self._id, self._modifiers, self._vk)
        except Exception:
            pass
        while True:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if msg.message == WM_USER:
                break
            if msg.message == WM_HOTKEY and msg.wParam == self._id:
                callback = self._callback
                if callback is not None:
                    callback()
        try:
            user32.UnregisterHotKey(None, self._id)
        except Exception:
            pass
