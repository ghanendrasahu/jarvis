from __future__ import annotations

import atexit
import os
import sys
import tempfile
from typing import Any

_MUTEX_HANDLES: list[Any] = []
_LOCK_FD: int | None = None
_LOCK_PATH: str | None = None


def _acquire_windows_mutex(name: str) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    # Keep the handle alive for the process lifetime so the mutex is held.
    _MUTEX_HANDLES.append(handle)
    return True


def _release_lockfile() -> None:
    global _LOCK_FD, _LOCK_PATH
    try:
        if _LOCK_FD is not None:
            os.close(_LOCK_FD)
            _LOCK_FD = None
        if _LOCK_PATH and os.path.exists(_LOCK_PATH):
            os.remove(_LOCK_PATH)
            _LOCK_PATH = None
    except OSError:
        pass


def _acquire_lockfile(name: str) -> bool:
    global _LOCK_FD, _LOCK_PATH
    path = os.path.join(tempfile.gettempdir(), f"jarvis_{name}.lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    _LOCK_FD = fd
    _LOCK_PATH = path
    atexit.register(_release_lockfile)
    return True


class SingleInstance:
    """Ensures at most one process holds a named application lock.

    Uses a Win32 named mutex on Windows (auto-released on process exit) and a
    tempdir lock file elsewhere. ``acquire()`` returns False when another
    instance already holds the lock.
    """

    def __init__(self, name: str) -> None:
        self.name = f"Jarvis_{name}"

    def acquire(self) -> bool:
        if sys.platform == "win32":
            return _acquire_windows_mutex(self.name)
        return _acquire_lockfile(self.name)
