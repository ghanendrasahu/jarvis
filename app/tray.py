from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - optional dependency
    pystray = None


def _make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(34, 211, 238, 255))
    d.ellipse((10, 10, 54, 54), fill=(5, 13, 29, 255))
    d.arc((24, 12, 46, 34), start=180, end=360, fill=(34, 211, 238, 255), width=6)
    d.line((39, 24, 39, 46), fill=(34, 211, 238, 255), width=6)
    d.line((39, 46, 29, 46), fill=(34, 211, 238, 255), width=6)
    return img


class TrayIcon:
    """System-tray icon via pystray (guarded import). Runs in a daemon thread."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_mini: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._on_show = on_show
        self._on_mini = on_mini
        self._on_exit = on_exit
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return pystray is not None

    @property
    def running(self) -> bool:
        return self._icon is not None

    def start(self) -> bool:
        if pystray is None or self._icon is not None:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Show HUD", lambda _i, _t: self._on_show()),
            pystray.MenuItem("Mini window", lambda _i, _t: self._on_mini()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda _i, _t: self._on_exit()),
        )
        self._icon = pystray.Icon("JARVIS", _make_icon_image(), "JARVIS", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
