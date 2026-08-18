from __future__ import annotations

import json
import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.monitor import ResourceMonitor

BG = "#020617"
PANEL = "#081225"
STROKE = "#1e2c52"
CYAN = "#22d3ee"
TEXT = "#dbeafe"
MUTED = "#8ea6ca"
GREEN = "#86efac"
AMBER = "#fbbf24"
RED = "#fb7185"


def voice_phase(status: dict[str, Any]) -> tuple[str, str, float, float]:
    """Derive a (label, color, amplitude, speed) tuple from the voice status dict."""
    if not status.get("running"):
        return "IDLE", MUTED, 6.0, 0.18
    if status.get("last_error"):
        return "ATTENTION", AMBER, 10.0, 0.26
    last_activity = status.get("last_activity_at")
    recent = False
    if last_activity:
        try:
            parsed = datetime.fromisoformat(str(last_activity))
            recent = (datetime.now(UTC) - parsed).total_seconds() < 4.0
        except Exception:
            recent = False
    if recent:
        return "PROCESSING", CYAN, 26.0, 0.34
    return "LISTENING", GREEN, 16.0, 0.24


class MiniHud:
    """Small frameless, always-on-top, draggable window showing live JARVIS activity."""

    def __init__(
        self,
        root: tk.Tk,
        runtime: Any,
        monitor: ResourceMonitor,
        on_run_command: Callable[[str], None],
        on_open_main: Callable[[], None],
    ) -> None:
        self.root = root
        self.runtime = runtime
        self.monitor = monitor
        self._on_run_command = on_run_command
        self._on_open_main = on_open_main
        self._config_path = Path(__file__).resolve().parent.parent / "data" / "mini_window.json"
        self._pinned = True
        self._drag_off_x: int | None = None
        self._drag_off_y: int | None = None
        self._active_command = ""
        self._last_status = ""

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        self.win.configure(bg=BG)
        self.win.bind("<Button-1>", self._drag_start)
        self.win.bind("<B1-Motion>", self._drag_move)

        self._build()
        self._restore_position()
        self.win.after(300, self._tick)

    def _build(self) -> None:
        shell = tk.Frame(self.win, bg=BG, highlightbackground=STROKE, highlightthickness=1, bd=0)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(header, text="JARVIS", bg=BG, fg="#d7e6ff", font=("JetBrains Mono", 11, "bold")).pack(side="left")
        self.dot = tk.Canvas(header, width=14, height=14, bg=BG, highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(8, 0))
        self.dot.create_oval(3, 3, 11, 11, fill=MUTED, outline="", tags=("dot",))
        self._pin_btn = tk.Button(
            header, text="PIN", command=self._toggle_pin, bg="#0f1f3c", fg=TEXT, relief=tk.FLAT, bd=0,
            padx=6, pady=2, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        )
        self._pin_btn.pack(side="right")
        tk.Button(
            header, text="MAIN", command=self._on_open_main, bg="#0f1f3c", fg=TEXT, relief=tk.FLAT, bd=0,
            padx=6, pady=2, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).pack(side="right", padx=(0, 4))
        tk.Button(
            header, text="X", command=self.hide, bg="#3a1a24", fg=RED, relief=tk.FLAT, bd=0,
            padx=6, pady=2, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).pack(side="right", padx=(0, 6))

        self.cmd_label = tk.Label(
            shell, text="ready", bg=BG, fg=CYAN, font=("JetBrains Mono", 9), anchor="w",
            justify="left", wraplength=300,
        )
        self.cmd_label.pack(fill="x", padx=10, pady=(2, 1))
        self.res_label = tk.Label(
            shell, text="", bg=BG, fg=MUTED, font=("JetBrains Mono", 9), anchor="w",
            justify="left", wraplength=300,
        )
        self.res_label.pack(fill="x", padx=10, pady=(0, 4))

        bars = tk.Frame(shell, bg=BG)
        bars.pack(fill="x", padx=10, pady=(0, 4))
        self.cpu_label = tk.Label(bars, text="CPU --%", bg=BG, fg=TEXT, font=("JetBrains Mono", 8, "bold"))
        self.cpu_label.pack(side="left")
        self.cpu_bar = tk.Canvas(bars, width=90, height=8, bg="#0a1529", highlightthickness=0, bd=0)
        self.cpu_bar.pack(side="left", padx=(6, 10))
        self.ram_label = tk.Label(bars, text="RAM --%", bg=BG, fg=TEXT, font=("JetBrains Mono", 8, "bold"))
        self.ram_label.pack(side="left")
        self.ram_bar = tk.Canvas(bars, width=90, height=8, bg="#0a1529", highlightthickness=0, bd=0)
        self.ram_bar.pack(side="left", padx=(6, 0))

        foot = tk.Frame(shell, bg=BG)
        foot.pack(fill="x", padx=10, pady=(0, 8))
        entry_wrap = tk.Frame(foot, bg="#1c3257", padx=1, pady=1)
        entry_wrap.pack(side="left", fill="x", expand=True)
        self.entry = tk.Entry(
            entry_wrap, bg="#040a16", fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9)
        )
        self.entry.pack(fill="x", ipady=4)
        self.entry.bind("<Return>", lambda _: self._run_quick())
        tk.Button(
            foot, text="RUN", command=self._run_quick, bg=CYAN, fg="#021018", relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).pack(side="left", padx=(6, 0))

    def _drag_start(self, event: tk.Event) -> None:
        if isinstance(event.widget, (tk.Button, tk.Entry)):
            self._drag_off_x = None
            self._drag_off_y = None
            return
        self._drag_off_x = event.x_root - self.win.winfo_rootx()
        self._drag_off_y = event.y_root - self.win.winfo_rooty()

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag_off_x is None or self._drag_off_y is None:
            return
        x = event.x_root - self._drag_off_x
        y = event.y_root - self._drag_off_y
        self.win.geometry(f"+{x}+{y}")

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        self.win.attributes("-topmost", self._pinned)
        self._pin_btn.configure(text="PIN" if self._pinned else "UNPIN")

    def _run_quick(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, tk.END)
        self._on_run_command(text)

    def notify_command(self, command: str) -> None:
        self._active_command = command
        self.cmd_label.configure(text=f"NOW: {command}", fg=CYAN)
        self.res_label.configure(text="working\u2026", fg=AMBER)

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()

    def hide(self) -> None:
        self.win.withdraw()

    def toggle(self) -> None:
        if self.is_visible():
            self.hide()
        else:
            self.show()

    def is_visible(self) -> bool:
        return self.win.state() == "normal"

    def close(self) -> None:
        self._save_position()
        self.win.destroy()

    def _draw_bar(self, canvas: tk.Canvas, value: float, color: str) -> None:
        canvas.delete("all")
        w = int(canvas.winfo_width())
        h = int(canvas.winfo_height())
        fill_w = max(2, int((w * max(0.0, min(100.0, value))) / 100.0))
        canvas.create_rectangle(0, 0, fill_w, h, fill=color, outline="")

    def _tick(self) -> None:
        try:
            if not self.win.winfo_exists():
                return
            try:
                status = self.runtime.voice.status()
            except Exception:
                status = {}
            phase, color, _, _ = voice_phase(status)
            self.dot.itemconfigure("dot", fill=color)

            ctx: dict[str, Any] = {}
            try:
                ctx = self.runtime.memory.get_state("runtime_context", default={}) or {}
            except Exception:
                ctx = {}
            last_command = str(ctx.get("last_command") or "")
            last_status = str(ctx.get("last_status") or "")
            if self._active_command and last_command and last_command == self._active_command:
                self._active_command = ""
            if self._active_command:
                self.cmd_label.configure(text=f"NOW: {self._active_command}", fg=CYAN)
                self.res_label.configure(text="working\u2026", fg=AMBER)
            elif last_command:
                self._last_status = last_status
                res_color = GREEN if last_status in ("completed", "ok") else AMBER if last_status == "pending" else RED if last_status == "failed" else MUTED
                self.cmd_label.configure(text=f"LAST: {last_command}", fg=TEXT)
                self.res_label.configure(text=last_status.upper(), fg=res_color)
            else:
                self.cmd_label.configure(text="ready", fg=CYAN)
                self.res_label.configure(text="", fg=MUTED)

            snap = None
            try:
                snap = self.monitor.snapshot()
            except Exception:
                snap = None
            if snap is not None:
                self._draw_bar(self.cpu_bar, float(snap.cpu_percent), CYAN)
                self.cpu_label.configure(text=f"CPU {snap.cpu_percent:.0f}%")
                self._draw_bar(self.ram_bar, float(snap.memory_percent), AMBER)
                self.ram_label.configure(text=f"RAM {snap.memory_percent:.0f}%")
        finally:
            try:
                self.win.after(300, self._tick)
            except Exception:
                pass

    def _restore_position(self) -> None:
        x = y = None
        try:
            cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
            x = int(cfg.get("x", -1))
            y = int(cfg.get("y", -1))
            self._pinned = bool(cfg.get("pinned", True))
        except Exception:
            pass
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        if x is None or y is None or x < 0 or y < 0 or x > sw or y > sh:
            x = sw - 380
            y = 90
        self.win.geometry(f"+{x}+{y}")
        self.win.attributes("-topmost", self._pinned)
        self._pin_btn.configure(text="PIN" if self._pinned else "UNPIN")

    def _save_position(self) -> None:
        try:
            cfg = {"x": int(self.win.winfo_x()), "y": int(self.win.winfo_y()), "pinned": self._pinned}
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(json.dumps(cfg), encoding="utf-8")
        except Exception:
            pass
