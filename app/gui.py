from __future__ import annotations

import ctypes
import json
import math
import queue
import random
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Any

from app.bootstrap import build_runtime
from app.core.monitor import ResourceMonitor
from app.minihud import MiniHud, voice_phase

BG = "#020617"
PANEL = "#081225"
PANEL_2 = "#0b1730"
STROKE = "#1e2c52"
CYAN = "#22d3ee"
INDIGO = "#818cf8"
TEXT = "#dbeafe"
MUTED = "#8ea6ca"
GREEN = "#86efac"
AMBER = "#fbbf24"
RED = "#fb7185"


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.widget.bind("<Enter>", self._show, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")
        self.widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _: tk.Event | None = None) -> None:
        if self.tip is not None or not self.text.strip():
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.configure(bg="#0a1529")
        label = tk.Label(
            tip,
            text=self.text,
            bg="#0a1529",
            fg="#dbeafe",
            bd=1,
            relief="solid",
            highlightbackground="#24426d",
            highlightthickness=1,
            padx=8,
            pady=5,
            justify="left",
            font=("JetBrains Mono", 9),
        )
        label.pack()
        self.tip = tip

    def _hide(self, _: tk.Event | None = None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class JarvisHudApp:
    def __init__(self) -> None:
        self.runtime = build_runtime()
        self._monitor = ResourceMonitor()
        self._monitor.start()
        self.root = tk.Tk()
        self.root.title("JARVIS AI Command HUD")
        self.root.geometry("1500x920")
        self.root.minsize(1280, 820)
        self.root.configure(bg=BG)

        self._ui_config_path = Path(__file__).resolve().parent.parent / "data" / "jarvis_ui.json"
        self.mini = MiniHud(
            self.root, self.runtime, self._monitor, on_run_command=self._run_mini_command, on_open_main=self._raise_main_window
        )
        self.mini.hide()

        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy = False
        self._wave_phase = 0.0
        self._wave_active = False
        self._scene_angle = 0.0
        self._scene_particles: list[dict[str, Any]] = []
        self._pending_ids: list[str] = []
        self._tooltips: list[Tooltip] = []
        self._app_launch_items: list[dict[str, str]] = []
        self._all_app_launch_items: list[dict[str, str]] = []
        self._chat_streaming = False
        self._chat_history: list[dict[str, str]] = []
        self._cmd_history: list[str] = []
        self._cmd_history_index = -1

        self._build_ui()
        self._refresh_voice_button()
        self._refresh_auto_start_button()
        self._poll_events()
        self._animate_wave()
        self._animate_scene()
        self._append_terminal("SYS", "HUD online.")
        self._refresh_approvals()
        self._refresh_app_launcher()
        self._bind_hotkeys()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restore_ui_config()
        self.root.after(5000, self._job_tick)

    def _make_panel(self, parent: tk.Misc, bg_color: str = PANEL) -> tk.Frame:
        frame = tk.Frame(parent, bg=bg_color, highlightbackground=STROKE, highlightthickness=1, bd=0)
        c = tk.Canvas(frame, bg=bg_color, highlightthickness=0, bd=0, width=14, height=14)
        c.place(x=0, y=0)
        c.create_line(0, 12, 0, 0, fill=CYAN, width=1)
        c.create_line(0, 0, 12, 0, fill=CYAN, width=1)
        c2 = tk.Canvas(frame, bg=bg_color, highlightthickness=0, bd=0, width=14, height=14)
        c2.place(relx=1, rely=1, anchor="se")
        c2.create_line(14, 2, 14, 14, fill=INDIGO, width=1)
        c2.create_line(14, 14, 2, 14, fill=INDIGO, width=1)
        return frame

    def _chip(self, parent: tk.Misc, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg="#0f1f3c",
            fg=TEXT,
            activebackground="#183159",
            activeforeground="#f8fbff",
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            font=("JetBrains Mono", 10, "bold"),
            padx=12,
            pady=8,
        )

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = tk.Frame(self.root, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        top.columnconfigure(1, weight=1)
        tk.Label(top, text="JARVIS", bg=BG, fg="#d7e6ff", font=("JetBrains Mono", 22, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        dock = tk.Frame(top, bg=BG)
        dock.grid(row=0, column=1, sticky="e")
        for i, (label, cmd) in enumerate([("Play/Pause", "youtube pause"), ("Next", "youtube next"), ("Apps", "list apps")]):
            btn = self._chip(dock, label, lambda c=cmd: self._run_preset(c))
            btn.grid(row=0, column=i, padx=(0, 8))
            self._tooltips.append(Tooltip(btn, f"Run: {cmd}"))
        mini_btn = self._chip(dock, "MINI", self.mini.toggle)
        mini_btn.grid(row=0, column=3, padx=(0, 8))
        self._tooltips.append(Tooltip(mini_btn, "Show/hide the floating mini HUD"))
        self.auto_start_button = self._chip(dock, "AUTOSTART", self._toggle_auto_start)
        self.auto_start_button.grid(row=0, column=4, padx=(0, 8))
        self._tooltips.append(Tooltip(self.auto_start_button, "Toggle auto-start when this project opens / Windows login"))

        self.online_label = tk.Label(
            top, text="STATUS: OPERATIONAL", bg="#0d2332", fg=GREEN, font=("JetBrains Mono", 10, "bold"), padx=10, pady=5
        )
        self.online_label.grid(row=0, column=2, sticky="e")

        body = tk.Frame(self.root, bg=BG)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        body.columnconfigure(0, weight=11)
        body.columnconfigure(1, weight=4)
        body.columnconfigure(2, weight=4)
        body.rowconfigure(0, weight=1)

        left = self._make_panel(body, PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(4, weight=1)

        dock_panel = self._make_panel(left, PANEL_2)
        dock_panel.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        dock_panel.columnconfigure(1, weight=1)
        tk.Label(dock_panel, text="DOCK", bg=PANEL_2, fg="#c7d8f7", font=("JetBrains Mono", 12, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=8)
        dock_buttons = tk.Frame(dock_panel, bg=PANEL_2)
        dock_buttons.grid(row=0, column=1, sticky="w", padx=(6, 8))
        for i, (txt, cmd_text, tip) in enumerate(
            [
                ("▶", "youtube pause", "Toggle YouTube play/pause"),
                ("⏭", "youtube next", "Go to next YouTube video"),
                ("APPS", "list apps", "Show apps in the launcher panel"),
            ]
        ):
            b = self._chip(dock_buttons, txt, lambda c=cmd_text: self._run_preset(c))
            b.grid(row=0, column=i, padx=(0, 8))
            self._tooltips.append(Tooltip(b, tip))

        cmd = tk.Frame(left, bg=PANEL)
        cmd.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        cmd.columnconfigure(1, weight=1)
        tk.Label(cmd, text="COMMAND BAR", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.entry_wrap = tk.Frame(cmd, bg="#1c3257", padx=1, pady=1)
        self.entry_wrap.grid(row=0, column=1, sticky="ew")
        self.command_entry = tk.Entry(
            self.entry_wrap, bg="#040a16", fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 11)
        )
        self.command_entry.pack(fill="x", ipady=6)
        self.command_entry.bind("<Return>", lambda _: self._run_command())
        self.command_entry.bind("<Up>", lambda _: self._history_step(-1))
        self.command_entry.bind("<Down>", lambda _: self._history_step(1))
        self.run_button = tk.Button(cmd, text="EXECUTE", command=self._run_command, bg=CYAN, fg="#021018", relief=tk.FLAT, bd=0, padx=12, pady=7)
        self.run_button.grid(row=0, column=2, padx=(10, 0))

        resources = self._make_panel(left, PANEL_2)
        resources.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
        for col in range(3):
            resources.columnconfigure(col, weight=1)
        tk.Label(resources, text="RESOURCES", bg=PANEL_2, fg="#c7d8f7", font=("JetBrains Mono", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2), columnspan=3
        )
        self.cpu_canvas = tk.Canvas(resources, bg="#071227", height=72, highlightthickness=0)
        self.ram_canvas = tk.Canvas(resources, bg="#071227", height=72, highlightthickness=0)
        self.net_canvas = tk.Canvas(resources, bg="#071227", height=72, highlightthickness=0)
        tk.Label(resources, text="CPU USAGE", bg=PANEL_2, fg=TEXT, font=("JetBrains Mono", 9, "bold")).grid(row=1, column=0, sticky="w", padx=10)
        tk.Label(resources, text="RAM USAGE", bg=PANEL_2, fg=TEXT, font=("JetBrains Mono", 9, "bold")).grid(row=1, column=1, sticky="w", padx=10)
        tk.Label(resources, text="NETWORK IO", bg=PANEL_2, fg=TEXT, font=("JetBrains Mono", 9, "bold")).grid(row=1, column=2, sticky="w", padx=10)
        self.cpu_canvas.grid(row=2, column=0, sticky="ew", padx=(10, 6), pady=(2, 10))
        self.ram_canvas.grid(row=2, column=1, sticky="ew", padx=6, pady=(2, 10))
        self.net_canvas.grid(row=2, column=2, sticky="ew", padx=(6, 10), pady=(2, 10))

        feed_shell = self._make_panel(left, PANEL_2)
        feed_shell.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        feed_shell.columnconfigure(0, weight=1)
        tk.Label(feed_shell, text="ACTIVITY FEED", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2)
        )
        self.feed_text = tk.Text(feed_shell, bg="#030915", fg=TEXT, relief=tk.FLAT, bd=0, height=7, font=("Consolas", 9))
        self.feed_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.feed_text.configure(state=tk.DISABLED)
        for tag, color in [("feed_cmd", CYAN), ("feed_ok", GREEN), ("feed_pending", AMBER), ("feed_err", RED), ("feed_sys", MUTED)]:
            self.feed_text.tag_configure(tag, foreground=color)

        term_shell = self._make_panel(left, PANEL_2)
        term_shell.grid(row=4, column=0, sticky="nsew", padx=14, pady=(0, 14))
        term_shell.rowconfigure(1, weight=1)
        term_shell.columnconfigure(0, weight=1)
        tk.Label(term_shell, text="LIVE TERMINAL", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4)
        )
        self.terminal = tk.Text(term_shell, bg="#030915", fg="#a9cffd", relief=tk.FLAT, bd=0, wrap="word", font=("Fira Code", 10))
        self.terminal.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.terminal.configure(state=tk.DISABLED)
        for tag, color in [("ts", "#7c8aa8"), ("cmd", CYAN), ("ok", GREEN), ("pending", AMBER), ("err", RED), ("sys", INDIGO)]:
            self.terminal.tag_configure(tag, foreground=color)

        mid = self._make_panel(body, PANEL)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(6, weight=1)
        mid.rowconfigure(8, weight=1)

        tk.Label(mid, text="VOICE ENGINE", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))
        wave_wrap = self._make_panel(mid, "#0b152b")
        wave_wrap.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.wave_canvas = tk.Canvas(wave_wrap, height=90, bg="#050d1d", highlightthickness=0, bd=0, cursor="hand2")
        self.wave_canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.wave_canvas.bind("<Button-1>", lambda _: self._toggle_voice())
        self.voice_switch_button = tk.Button(mid, text="VOICE OFF", command=self._toggle_voice, bg="#143049", fg=TEXT, relief=tk.FLAT, bd=0, padx=10, pady=6)
        self.voice_switch_button.grid(row=1, column=0, sticky="e", padx=20, pady=(0, 6))

        tk.Label(mid, text="AGENTIVE STATUS", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=2, column=0, sticky="w", padx=12)
        self.approval_box = tk.Listbox(mid, bg="#081022", fg=TEXT, relief=tk.FLAT, bd=0, font=("Consolas", 10), height=10)
        self.approval_box.grid(row=3, column=0, sticky="nsew", padx=12, pady=(6, 10))
        self.approval_box.bind("<Double-Button-1>", lambda _: self._approve_selected())

        tk.Label(mid, text="METRICS & INSIGHTS", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=4, column=0, sticky="w", padx=12, pady=(0, 4))
        insights = self._make_panel(mid, "#0b152b")
        insights.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 8))
        insights.columnconfigure(0, weight=1)
        insights.rowconfigure(0, weight=1)
        self.metrics_canvas = tk.Canvas(insights, bg="#050d1d", height=120, highlightthickness=0, bd=0)
        self.metrics_canvas.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        tk.Label(mid, text="AI BOT", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=6, column=0, sticky="w", padx=12, pady=(0, 6))
        chat_box = self._make_panel(mid, "#0b152b")
        chat_box.grid(row=7, column=0, sticky="nsew", padx=12, pady=(0, 6))
        chat_box.columnconfigure(0, weight=1)
        chat_box.rowconfigure(1, weight=1)
        tk.Label(chat_box, text="AI BOT // ASK ANYTHING", bg="#0b152b", fg=CYAN, font=("JetBrains Mono", 8, "bold")).grid(
            row=0, column=0, sticky="ew", padx=8, pady=(6, 2)
        )
        self.chat_text = tk.Text(chat_box, bg="#050d1d", fg=TEXT, relief=tk.FLAT, bd=0, wrap="word", font=("Fira Code", 9), height=8)
        self.chat_text.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.chat_text.configure(state=tk.DISABLED)
        self.chat_text.tag_configure("chat_user_label", foreground=CYAN, font=("JetBrains Mono", 8, "bold"))
        self.chat_text.tag_configure("chat_user", foreground="#d8f3ff")
        self.chat_text.tag_configure("chat_ai_label", foreground=INDIGO, font=("JetBrains Mono", 8, "bold"))
        self.chat_text.tag_configure("chat_ai", foreground="#ecf2ff")
        self.chat_text.tag_configure("chat_stream", foreground="#ecf2ff")
        chat_input = tk.Frame(mid, bg=PANEL)
        chat_input.grid(row=8, column=0, sticky="ew", padx=12, pady=(0, 12))
        chat_input.columnconfigure(0, weight=1)
        shell = tk.Frame(chat_input, bg="#203662", padx=1, pady=1)
        shell.grid(row=0, column=0, sticky="ew")
        shell.columnconfigure(0, weight=1)
        self.chat_entry = tk.Entry(shell, bg="#050d1d", fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 10))
        self.chat_entry.grid(row=0, column=0, sticky="ew", ipady=7)
        self.chat_entry.bind("<Return>", lambda _: self._ask_chatbot())
        tk.Button(chat_input, text="ASK", command=self._ask_chatbot, bg=INDIGO, fg="#05101b", relief=tk.FLAT, bd=0, padx=10, pady=6).grid(row=0, column=1, padx=(8, 0))

        right = self._make_panel(body, PANEL)
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(5, weight=1)
        tk.Label(right, text="JOB AGENT", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 8))
        job_panel = self._make_panel(right, "#0b152b")
        job_panel.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        job_panel.columnconfigure(0, weight=1)
        self.job_status_label = tk.Label(
            job_panel, text="No job-application task running.", bg="#0b152b", fg=MUTED,
            font=("JetBrains Mono", 8), justify="left", anchor="w", wraplength=280,
        )
        self.job_status_label.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        job_stats = tk.Frame(job_panel, bg="#0b152b")
        job_stats.grid(row=1, column=0, sticky="ew", padx=8)
        self.job_stat_labels: dict[str, tk.Label] = {}
        for i, (name, col) in enumerate([("APPLIED", GREEN), ("SKIPPED", AMBER), ("FAILED", RED)]):
            lbl = tk.Label(job_stats, text=f"{name} 0", bg="#0b152b", fg=col, font=("JetBrains Mono", 8, "bold"))
            lbl.grid(row=0, column=i, padx=(0, 12), sticky="w")
            self.job_stat_labels[name] = lbl
        job_ctrl = tk.Frame(job_panel, bg="#0b152b")
        job_ctrl.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 6))
        job_ctrl.columnconfigure(0, weight=1)
        self.job_query_entry = tk.Entry(
            job_ctrl, bg="#050d1d", fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9)
        )
        self.job_query_entry.grid(row=0, column=0, sticky="ew", ipady=4)
        self.job_query_entry.bind("<Return>", lambda _: self._start_job_apply())
        tk.Button(
            job_ctrl, text="APPLY", command=self._start_job_apply, bg=CYAN, fg="#021018", relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=1, padx=(6, 0))
        tk.Button(
            job_ctrl, text="STATUS", command=self._refresh_job_status, bg="#143049", fg=TEXT, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=2, padx=(6, 0))
        tk.Button(
            job_ctrl, text="CANCEL", command=self._cancel_job, bg="#3a1a24", fg=RED, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=3, padx=(6, 0))
        tk.Label(right, text="AVAILABLE APPS  (ALT+1..ALT+9)", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(row=2, column=0, sticky="w", padx=12, pady=(4, 6))
        self.app_filter_var = tk.StringVar()
        self.app_filter_var.trace_add("write", lambda *_: self._apply_app_filter())
        tk.Entry(right, textvariable=self.app_filter_var, bg="#081022", fg=TEXT, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9)).grid(
            row=2, column=0, sticky="e", padx=12, ipady=4
        )
        self.app_listbox = tk.Listbox(right, bg="#081022", fg=TEXT, relief=tk.FLAT, bd=0, font=("Consolas", 10), height=8)
        self.app_listbox.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.app_listbox.bind("<Double-Button-1>", lambda _: self._open_selected_app())
        self.app_listbox.bind("<Return>", lambda _: self._open_selected_app())
        tk.Label(right, text="SCENE VIEWER", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 10, "bold")).grid(row=4, column=0, sticky="w", padx=12)
        self.scene = tk.Canvas(right, bg="#050b16", highlightthickness=0, bd=0)
        self.scene.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.scene.bind("<Configure>", self._reset_scene)
        self._draw_resource_panels()

    def _draw_resource_panels(self) -> None:
        for c in [getattr(self, "cpu_canvas", None), getattr(self, "ram_canvas", None), getattr(self, "net_canvas", None)]:
            if c is None:
                return

        chart = self._monitor.chart()
        cpu_history = chart["cpu_history"]
        mem_history = chart["memory_history"]

        def _spark(canvas: tk.Canvas, line_color: str, values: list[float], unit: str = "%", bars: bool = False) -> None:
            canvas.delete("all")
            w = max(180, canvas.winfo_reqwidth())
            h = max(72, canvas.winfo_reqheight())
            canvas.create_rectangle(0, 0, w, h, fill="#071227", outline="")
            if not values:
                canvas.create_text(w // 2, h // 2, text="sampling...", fill="#3f5d8a", font=("JetBrains Mono", 9))
                return
            if bars:
                bw = max(6, w // max(1, len(values)))
                x = 4
                for v in values[-20:]:
                    vh = max(4, int((v / 100.0) * (h - 16)))
                    canvas.create_rectangle(x, h - 8 - vh, x + bw, h - 8, fill="#58d6ff", outline="")
                    x += bw + 2
            else:
                pts: list[float] = []
                step = max(1.0, w / max(1, (len(values) - 1)))
                for i, v in enumerate(values):
                    y = (h - 8) - ((v / 100.0) * (h - 20))
                    pts.extend([4 + i * step, y])
                if len(pts) >= 4:
                    canvas.create_line(pts, fill=line_color, width=2, smooth=True)
                canvas.create_line(0, h - 8, w, h - 8, fill="#1f355d", width=1)
            latest = values[-1] if values else 0.0
            canvas.create_text(w - 6, 10, anchor="ne", text=f"{latest:.0f}{unit}", fill="#7fdfff", font=("JetBrains Mono", 9, "bold"))

        _spark(self.cpu_canvas, "#59d7ff", cpu_history, "%")
        _spark(self.ram_canvas, "#8fdcff", mem_history, "%", bars=True)
        net_sent = [chart["net_sent_kbps"]]
        net_recv = [chart["net_recv_kbps"]]
        net_values = net_sent + net_recv if len(net_sent) < 2 else (net_sent + net_recv)[-60:]
        _spark(self.net_canvas, "#63e8ff", net_values, "kb/s")

        self.metrics_canvas.delete("all")
        sample = self._monitor.snapshot()
        self.metrics_canvas.create_oval(12, 16, 108, 112, outline="#2a426d", width=12)
        cpu_extent = max(0, min(360, int((sample.cpu_percent / 100.0) * 360)))
        self.metrics_canvas.create_arc(12, 16, 108, 112, start=90, extent=-cpu_extent, outline="#5fd7ff", style=tk.ARC, width=12)
        self.metrics_canvas.create_text(130, 36, anchor="w", text=f"CPU {sample.cpu_percent:.0f}%", fill="#7fdfff", font=("JetBrains Mono", 9, "bold"))
        self.metrics_canvas.create_text(130, 58, anchor="w", text=f"MEM {sample.memory_percent:.0f}%", fill="#f8c763", font=("JetBrains Mono", 9, "bold"))
        self.metrics_canvas.create_text(
            130, 80, anchor="w",
            text=f"NET {sample.net_sent_kbps:.0f}/{sample.net_recv_kbps:.0f}",
            fill="#9fb3d8", font=("JetBrains Mono", 9, "bold"),
        )

    def _append_terminal(self, kind: str, text: str) -> None:
        self.terminal.configure(state=tk.NORMAL)
        self.terminal.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] ", ("ts",))
        tag = {"CMD": "cmd", "OK": "ok", "WARN": "pending", "ERR": "err", "SYS": "sys"}.get(kind, "sys")
        self.terminal.insert(tk.END, text + "\n", (tag,))
        self.terminal.see(tk.END)
        self.terminal.configure(state=tk.DISABLED)

    def _append_feed(self, tag: str, text: str) -> None:
        self.feed_text.configure(state=tk.NORMAL)
        self.feed_text.insert(tk.END, text + "\n", (tag,))
        lines = int(self.feed_text.index("end-1c").split(".")[0])
        if lines > 40:
            self.feed_text.delete("1.0", f"{lines - 30}.0")
        self.feed_text.see(tk.END)
        self.feed_text.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.run_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _run_preset(self, command: str) -> None:
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, command)
        self._run_command()

    def _run_command(self) -> None:
        if self._busy:
            return
        command = self.command_entry.get().strip()
        if not command:
            return
        self.command_entry.delete(0, tk.END)
        self._run_command_text(command)

    def _run_mini_command(self, command: str) -> None:
        self._run_command_text(command)

    def _run_command_text(self, command: str) -> None:
        if not command or self._busy:
            return
        self._push_history(command)
        self._append_terminal("CMD", f"> {command}")
        self._append_feed("feed_cmd", f"\u25b6 {command}")
        self.mini.notify_command(command)
        self.mini.show()
        self._set_busy(True)

        def worker() -> None:
            try:
                result = self.runtime.orchestrator.execute_command(command=command, source="gui", auto_approve=False)
                self._events.put(("command_result", result.model_dump()))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _push_history(self, command: str) -> None:
        if command in self._cmd_history:
            self._cmd_history.remove(command)
        self._cmd_history.insert(0, command)
        self._cmd_history = self._cmd_history[:50]
        self._cmd_history_index = -1

    def _history_step(self, direction: int) -> None:
        if not self._cmd_history:
            return
        n = len(self._cmd_history)
        idx = min(max(self._cmd_history_index + direction, 0), n - 1)
        self._cmd_history_index = idx
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, self._cmd_history[idx])
        self.command_entry.icursor(tk.END)

    def _start_job_apply(self) -> None:
        query = self.job_query_entry.get().strip()
        if not query:
            return
        self.job_query_entry.delete(0, tk.END)
        self._run_command_text(f"apply for {query}")

    def _refresh_job_status(self) -> None:
        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("job_status", {})
                self._events.put(("job_status", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_job(self) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("job_cancel", {})
                self._events.put(("job_status", payload))
                self._events.put(("job_msg", payload.get("result_text", "Job task canceled.")))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _job_tick(self) -> None:
        try:
            if not self.root.winfo_exists():
                return
            self._refresh_job_status()
        finally:
            self.root.after(5000, self._job_tick)

    def _raise_main_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _restore_ui_config(self) -> None:
        try:
            cfg = json.loads(self._ui_config_path.read_text(encoding="utf-8"))
            geo = cfg.get("main_geometry")
            if geo and geo.count("x") == 1 and "+" in geo:
                self.root.geometry(geo)
            if cfg.get("mini_visible"):
                self.mini.show()
        except Exception:
            pass

    def _save_ui_config(self) -> None:
        try:
            cfg = {"main_geometry": self.root.geometry(), "mini_visible": self.mini.is_visible()}
            self._ui_config_path.parent.mkdir(parents=True, exist_ok=True)
            self._ui_config_path.write_text(json.dumps(cfg), encoding="utf-8")
        except Exception:
            pass

    def _auto_start_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "auto_start.json"

    def _auto_start_enabled(self) -> bool:
        try:
            cfg = json.loads(self._auto_start_path().read_text(encoding="utf-8"))
            return bool(cfg.get("enabled", True))
        except Exception:
            return True

    def _write_auto_start(self, enabled: bool) -> None:
        try:
            path = self._auto_start_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
        except Exception:
            pass

    def _toggle_auto_start(self) -> None:
        self._write_auto_start(not self._auto_start_enabled())
        self._refresh_auto_start_button()

    def _refresh_auto_start_button(self) -> None:
        on = self._auto_start_enabled()
        self.auto_start_button.configure(
            text="AUTOSTART ON" if on else "AUTOSTART OFF",
            bg="#0f1f3c" if on else "#3a1a24",
            fg=GREEN if on else AMBER,
        )

    def _toggle_voice(self) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                if self.runtime.voice.is_running():
                    changed = self.runtime.voice.stop()
                    self._events.put(("voice", {"running": False, "changed": changed}))
                else:
                    changed = self.runtime.voice.start()
                    self._events.put(("voice", {"running": True, "changed": changed}))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_voice_button(self) -> None:
        self._wave_active = self.runtime.voice.is_running()
        if self._wave_active:
            self.voice_switch_button.configure(text="VOICE ON", bg="#173f2e", fg=GREEN)
            self.online_label.configure(text="STATUS: STREAMING")
        else:
            self.voice_switch_button.configure(text="VOICE OFF", bg="#143049", fg=TEXT)
            self.online_label.configure(text="STATUS: ONLINE")

    def _animate_wave(self) -> None:
        self.wave_canvas.delete("all")
        w = max(1, self.wave_canvas.winfo_width())
        h = max(1, self.wave_canvas.winfo_height())
        c = h // 2
        try:
            status = self.runtime.voice.status()
        except Exception:
            status = {}
        phase, color, amp, speed = voice_phase(status)
        self._wave_phase += speed
        pts: list[float] = []
        for x in range(0, w + 1, 6):
            pts.extend([x, c + math.sin((x * 0.05) + self._wave_phase) * amp])
        if len(pts) >= 4:
            self.wave_canvas.create_line(pts, fill=color, width=2, smooth=True)
        self.wave_canvas.create_text(10, 10, anchor="nw", text=f"VOICE {phase}", fill=color, font=("JetBrains Mono", 9, "bold"))
        now = time.monotonic()
        if now - getattr(self, "_last_resource_draw", 0.0) >= 1.0:
            self._last_resource_draw = now
            self._draw_resource_panels()
        self.root.after(50, self._animate_wave)

    def _refresh_approvals(self) -> None:
        def worker() -> None:
            try:
                items = [i.model_dump() for i in self.runtime.orchestrator.list_pending_approvals()]
                self._events.put(("approvals", items))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _approve_selected(self) -> None:
        sel = self.approval_box.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._pending_ids):
            return
        approval_id = self._pending_ids[idx]
        self._set_busy(True)

        def worker() -> None:
            try:
                res = self.runtime.orchestrator.approve(approval_id)
                self._events.put(("approval_result", res.model_dump()))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _bind_hotkeys(self) -> None:
        for i in range(1, 10):
            self.root.bind_all(f"<Alt-Key-{i}>", lambda e, idx=i - 1: self._open_app_by_index(idx))

    def _refresh_app_launcher(self) -> None:
        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("list_known_apps", {})
                self._events.put(("app_launcher", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_app_launcher(self, payload: dict[str, Any]) -> None:
        self._all_app_launch_items = []
        for item in payload.get("desktop_apps") or []:
            alias = str(item.get("alias", "")).strip()
            if alias:
                self._all_app_launch_items.append({"command": f"open {alias}", "label": f"{alias} {'[READY]' if item.get('available') else '[MAY OPEN]'}"})
        for item in payload.get("web_apps") or []:
            alias = str(item.get("alias", "")).strip()
            if alias:
                self._all_app_launch_items.append({"command": f"open {alias}", "label": f"{alias} [WEB]"})
        self._all_app_launch_items = self._all_app_launch_items[:60]
        self._apply_app_filter()

    def _apply_app_filter(self) -> None:
        q = (self.app_filter_var.get() or "").strip().lower()
        self.app_listbox.delete(0, tk.END)
        self._app_launch_items = []
        for item in self._all_app_launch_items:
            if q and q not in item["label"].lower() and q not in item["command"].lower():
                continue
            self._app_launch_items.append(item)
        for idx, item in enumerate(self._app_launch_items):
            hk = f"Alt+{idx+1}" if idx < 9 else "      "
            self.app_listbox.insert(tk.END, f"{hk:<7}  {item['label']}")
        if not self._app_launch_items:
            self.app_listbox.insert(tk.END, "No apps match filter.")

    def _open_selected_app(self) -> None:
        sel = self.app_listbox.curselection()
        if not sel:
            return
        self._open_app_by_index(sel[0])

    def _open_app_by_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._app_launch_items):
            return
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, self._app_launch_items[idx]["command"])
        self._run_command()

    def _append_chat(self, role: str, text: str) -> None:
        self.chat_text.configure(state=tk.NORMAL)
        if role == "user":
            self.chat_text.insert(tk.END, "You\n", ("chat_user_label",))
            self.chat_text.insert(tk.END, text + "\n\n", ("chat_user",))
        else:
            self.chat_text.insert(tk.END, "AI Bot\n", ("chat_ai_label",))
            self.chat_text.insert(tk.END, text + "\n\n", ("chat_ai",))
        self.chat_text.see(tk.END)
        self.chat_text.configure(state=tk.DISABLED)

    def _start_chat_stream(self) -> None:
        self.chat_text.configure(state=tk.NORMAL)
        self.chat_text.insert(tk.END, "AI Bot\n", ("chat_ai_label",))
        self.chat_text.configure(state=tk.DISABLED)
        self._chat_streaming = True

    def _append_chat_stream_chunk(self, chunk: str) -> None:
        if not self._chat_streaming:
            return
        self.chat_text.configure(state=tk.NORMAL)
        self.chat_text.insert(tk.END, chunk, ("chat_stream",))
        self.chat_text.see(tk.END)
        self.chat_text.configure(state=tk.DISABLED)

    def _finish_chat_stream(self) -> None:
        if not self._chat_streaming:
            return
        self.chat_text.configure(state=tk.NORMAL)
        self.chat_text.insert(tk.END, "\n\n", ("chat_stream",))
        self.chat_text.configure(state=tk.DISABLED)
        self._chat_streaming = False

    def _ask_chatbot(self) -> None:
        q = self.chat_entry.get().strip()
        if not q:
            return
        self.chat_entry.delete(0, tk.END)
        self._append_chat("user", q)
        self._set_busy(True)
        self._start_chat_stream()
        self._chat_history.append({"role": "user", "content": q})
        self._chat_history = self._chat_history[-12:]

        def worker() -> None:
            try:
                if not self._stream_chatbot_openai(q):
                    hist = self.runtime.memory.recent_conversation(limit=12)
                    plan = self.runtime.planner.plan(q, [], hist)
                    ans = (plan.final_response or "").strip()
                    if not ans:
                        ans = self.runtime.orchestrator.execute_command(command=q, source="gui_chat", auto_approve=False).response_text
                    self._events.put(("chat_stream_chunk", ans))
                    self._events.put(("chat_stream_done", ans))
            except Exception as exc:
                self._events.put(("chat_stream_chunk", f"Error: {exc}"))
                self._events.put(("chat_stream_done", f"Error: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _stream_chatbot_openai(self, q: str) -> bool:
        if str(getattr(self.runtime.settings, "llm_provider", "")).lower() != "openai" or not getattr(self.runtime.settings, "openai_api_key", None):
            return False
        try:
            from openai import OpenAI
        except Exception:
            return False
        client = OpenAI(api_key=self.runtime.settings.openai_api_key)
        model = str(getattr(self.runtime.settings, "openai_model", "gpt-4.1-mini"))
        msgs: list[dict[str, str]] = [{"role": "system", "content": "You are AI Bot inside Jarvis HUD. Keep answers concise and useful."}]
        msgs.extend(self._chat_history[-10:])
        msgs.append({"role": "user", "content": q})
        stream = client.chat.completions.create(model=model, messages=msgs, stream=True, temperature=0.3)
        built: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices and chunk.choices[0].delta else None
            if not delta:
                continue
            built.append(delta)
            self._events.put(("chat_stream_chunk", delta))
        self._events.put(("chat_stream_done", "".join(built).strip()))
        return True

    def _reset_scene(self, _: tk.Event | None = None) -> None:
        self.scene.delete("all")
        w = max(1, self.scene.winfo_width())
        h = max(1, self.scene.winfo_height())
        for i, c in enumerate(["#050a14", "#081020", "#0b1629", "#0e1b33"]):
            y0 = int((i / 4) * h)
            y1 = int(((i + 1) / 4) * h)
            self.scene.create_rectangle(0, y0, w, y1, fill=c, outline="")
        cx, cy = int(w * 0.68), int(h * 0.72)
        self.scene.create_oval(cx - 90, cy - 90, cx + 90, cy + 90, outline=INDIGO, width=1, tags="ring")
        self.scene.create_oval(cx - 120, cy - 120, cx + 120, cy + 120, outline="#22416a", width=1, tags="ring2")
        self.scene.create_rectangle(cx - 86, cy - 8, cx + 86, cy + 2, fill="#6fd9ff", outline="")
        self.scene.create_rectangle(cx - 58, cy - 8, cx - 48, cy + 72, fill="#78e1ff", outline="")
        self.scene.create_rectangle(cx + 48, cy - 8, cx + 58, cy + 72, fill="#78e1ff", outline="")
        self._scene_particles.clear()
        for _ in range(20):
            x = random.uniform(0, w)
            y = random.uniform(0, h)
            r = random.uniform(2, 5)
            speed = random.uniform(0.2, 0.9)
            node = self.scene.create_oval(x - r, y - r, x + r, y + r, fill="#3ec7ff", outline="")
            self._scene_particles.append({"id": node, "x": x, "y": y, "r": r, "speed": speed})

    def _animate_scene(self) -> None:
        if not self.root.winfo_exists():
            return
        w = max(1, self.scene.winfo_width())
        h = max(1, self.scene.winfo_height())
        cx, cy = int(w * 0.68), int(h * 0.72)
        self._scene_angle += 0.03
        r = 90 + (3 * math.sin(self._scene_angle))
        r2 = 120 + (4 * math.cos(self._scene_angle * 0.7))
        self.scene.coords("ring", cx - r, cy - r, cx + r, cy + r)
        self.scene.coords("ring2", cx - r2, cy - r2, cx + r2, cy + r2)
        for p in self._scene_particles:
            p["y"] += p["speed"]
            p["x"] += math.sin(self._scene_angle + p["y"] * 0.02) * 0.5
            if p["y"] > h + 8:
                p["y"] = -8
                p["x"] = random.uniform(0, w)
            x, y, r0 = p["x"], p["y"], p["r"]
            self.scene.coords(p["id"], x - r0, y - r0, x + r0, y + r0)
        self.root.after(38, self._animate_scene)

    def _handle_command_result(self, payload: dict[str, Any]) -> None:
        self._set_busy(False)
        self._append_terminal("OK", payload.get("response_text", ""))
        has_failure = False
        has_pending = False
        for item in payload.get("results") or []:
            tool = item.get("tool")
            status = item.get("status")
            if status == "completed":
                self._append_terminal("OK", f"{tool}: completed")
            elif status == "pending":
                self._append_terminal("WARN", f"{tool}: pending approval")
                has_pending = True
            else:
                self._append_terminal("ERR", f"{tool}: {item.get('error')}")
                has_failure = True
        command = str(payload.get("command", "command"))
        if has_failure:
            self._append_feed("feed_err", f"\u2717 {command} FAILED")
        elif has_pending:
            self._append_feed("feed_pending", f"\u23f3 {command} awaiting approval")
        else:
            self._append_feed("feed_ok", f"\u2713 {command} done")
        self._refresh_approvals()

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if event == "command_result":
                self._handle_command_result(payload)
            elif event == "voice":
                self._set_busy(False)
                self._wave_active = bool(payload.get("running"))
                self._refresh_voice_button()
            elif event == "approvals":
                self.approval_box.delete(0, tk.END)
                self._pending_ids = []
                for item in payload:
                    aid = str(item.get("approval_id", ""))
                    tool = str(item.get("tool", ""))
                    self.approval_box.insert(tk.END, f"{aid}... {tool}")
                    self._pending_ids.append(aid)
                if not payload:
                    self.approval_box.insert(tk.END, "No pending approvals.")
            elif event == "approval_result":
                self._set_busy(False)
                self._append_terminal("OK", payload.get("message", ""))
                self._refresh_approvals()
            elif event == "app_launcher":
                self._handle_app_launcher(payload)
            elif event == "chat_stream_chunk":
                self._append_chat_stream_chunk(str(payload))
            elif event == "chat_stream_done":
                self._finish_chat_stream()
                self._set_busy(False)
                done = str(payload or "").strip()
                if done:
                    self._chat_history.append({"role": "assistant", "content": done})
                    self._chat_history = self._chat_history[-12:]
            elif event == "job_status":
                self._update_job_panel(payload)
            elif event == "job_msg":
                self._set_busy(False)
                self._append_terminal("OK", str(payload))
                self._append_feed("feed_sys", str(payload))
            elif event == "error":
                self._set_busy(False)
                self._append_terminal("ERR", str(payload))
        self.root.after(80, self._poll_events)

    def _update_job_panel(self, payload: dict[str, Any]) -> None:
        task = payload.get("task") or {}
        status = str(task.get("status") or "idle")
        phase = str(task.get("phase") or "-")
        applied = int(task.get("applied") or 0)
        skipped = int(task.get("skipped") or 0)
        failed = int(task.get("failed") or 0)
        reason = str(task.get("paused_reason") or "").strip()
        color = {"idle": MUTED, "running": CYAN, "paused": AMBER, "completed": GREEN, "canceled": MUTED}.get(status, MUTED)
        text = f"[{status.upper()} \u00b7 {phase}]"
        if reason:
            text += f"\nPaused: {reason}"
        self.job_status_label.configure(text=text, fg=color)
        self.job_stat_labels["APPLIED"].configure(text=f"APPLIED {applied}")
        self.job_stat_labels["SKIPPED"].configure(text=f"SKIPPED {skipped}")
        self.job_stat_labels["FAILED"].configure(text=f"FAILED {failed}")

    def _on_close(self) -> None:
        try:
            self.runtime.voice.stop()
        except Exception:
            pass
        try:
            self._monitor.stop()
        except Exception:
            pass
        self._save_ui_config()
        try:
            self.mini.close()
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> None:
    _enable_dpi_awareness()
    JarvisHudApp().run()


if __name__ == "__main__":
    main()
