from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

BG = "#020617"
STROKE = "#1e2c52"
TEXT = "#dbeafe"
MUTED = "#8ea6ca"


class Toaster:
    """Frameless, topmost toast shown in the corner of the main window."""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._win: tk.Toplevel | None = None
        self._after_id: str | None = None

    def notify(
        self,
        title: str,
        message: str,
        color: str = "#fbbf24",
        duration_ms: int = 6500,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self._dismiss()
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        frame = tk.Frame(win, bg="#0a1529", highlightbackground=STROKE, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        head = tk.Frame(frame, bg="#0a1529")
        head.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(head, text=title, bg="#0a1529", fg=color, font=("JetBrains Mono", 9, "bold")).pack(side="left")
        close = tk.Label(head, text="x", bg="#0a1529", fg=MUTED, font=("JetBrains Mono", 10, "bold"), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self._dismiss())
        msg = tk.Label(
            frame, text=message, bg="#0a1529", fg=TEXT, font=("JetBrains Mono", 9),
            justify="left", anchor="w", wraplength=320,
        )
        msg.pack(fill="x", padx=10, pady=(2, 10))
        if on_click is not None:
            def _handle_click() -> None:
                self._dismiss()
                on_click()

            msg.bind("<Button-1>", lambda _e: _handle_click())
            frame.bind("<Button-1>", lambda _e: _handle_click(), add="+")
        self.root.update_idletasks()
        try:
            width = max(340, frame.winfo_reqwidth() + 16)
            height = frame.winfo_reqheight() + 14
            x = self.root.winfo_rootx() + self.root.winfo_width() - width - 16
            y = self.root.winfo_rooty() + 10
        except Exception:
            width, height, x, y = 340, 96, 40, 40
        win.geometry(f"{width}x{height}+{max(0, x)}+{y}")
        self._win = win
        self._after_id = self.root.after(duration_ms, self._dismiss)

    def _dismiss(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
