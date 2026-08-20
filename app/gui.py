from __future__ import annotations

import ctypes
import json
import math
import queue
import random
import shutil
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from app.bootstrap import build_runtime
from app.core.events import EventHub
from app.core.media import background_image_path, list_background_images
from app.core.palette import PALETTES, THEME_ORDER
from app.hotkey import GlobalHotkey
from app.minihud import MiniHud, voice_phase
from app.toast import Toaster
from app.tray import TrayIcon

BG = PANEL = PANEL_2 = STROKE = CYAN = INDIGO = TEXT = MUTED = GREEN = AMBER = RED = ""
INNER = CANVAS = TERM = LIST = ENTRY = CHIP = CHIP_HI = SEL = BTN2 = DANGER = OK = STATUS = SHELL = TOOLTIP = ""
TOOLTIP_BORDER = SCENE = RING2 = ""

_current_theme = "aurora"


def _apply_palette(theme: str) -> None:
    global _current_theme
    global BG, PANEL, PANEL_2, STROKE, CYAN, INDIGO, TEXT, MUTED, GREEN, AMBER, RED
    global INNER, CANVAS, TERM, LIST, ENTRY, CHIP, CHIP_HI, SEL, BTN2, DANGER, OK, STATUS, SHELL, TOOLTIP
    global TOOLTIP_BORDER, SCENE, RING2
    _current_theme = theme if theme in PALETTES else "aurora"
    for name, value in PALETTES[_current_theme].items():
        globals()[name] = value


_apply_palette(_current_theme)


def _backgrounds_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "backgrounds"


def _unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


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
        tip.configure(bg=TOOLTIP)
        label = tk.Label(
            tip,
            text=self.text,
            bg=TOOLTIP,
            fg=TEXT,
            bd=1,
            relief="solid",
            highlightbackground=TOOLTIP_BORDER,
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
        self._hub = EventHub()
        self.runtime = build_runtime(events=self._hub)
        self._monitor = self.runtime.monitor
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
        self._last_pending_count = 0
        self._job_prev_status = ""
        self._theme = "aurora"
        self._gfx_full = False
        self._bg: dict[str, str] = {"mode": "aurora", "value": ""}
        self._approval_badge: tk.Label | None = None
        self._approval_cards: dict[str, tk.Frame] = {}
        self._top_frame: tk.Frame | None = None
        self._hub.subscribe(self._on_hub_event)

        self._build_ui()
        self._post_build()
        self._toaster = Toaster(self.root)
        self._tray = TrayIcon(on_show=self._tray_show, on_mini=self._tray_mini, on_exit=self._tray_exit)
        self._hotkey = GlobalHotkey()
        if self._hotkey.start(lambda: self._events.put(("summon_mini", None))):
            self._append_terminal("SYS", "Global hotkey Ctrl+Shift+J registered.")
        self._poll_events()
        self._animate_wave()
        self._animate_scene()
        self._append_terminal("SYS", "HUD online.")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restore_ui_config()
        self.runtime.scheduler.on_run = self._scheduler_on_run
        self.runtime.scheduler.start()
        self._maybe_show_recap()
        self.root.after(5000, self._job_tick)

    def _make_panel(self, parent: tk.Misc, bg_color: str = PANEL) -> tk.Frame:
        return tk.Frame(parent, bg=bg_color, highlightbackground=STROKE, highlightthickness=1, bd=0)

    def _hover_color(self, bg_color: str) -> str:
        try:
            return self._blend(bg_color, "#ffffff", 0.14)
        except Exception:
            return bg_color

    def _bind_hover(self, btn: tk.Button, base: str, hover: str) -> None:
        def _enter(_e: tk.Event, b: tk.Button = btn, c: str = hover) -> None:
            b.configure(bg=c)

        def _leave(_e: tk.Event, b: tk.Button = btn, c: str = base) -> None:
            b.configure(bg=c)

        btn.bind("<Enter>", _enter)
        btn.bind("<Leave>", _leave)

    def _header_chip(self, parent: tk.Misc, text: str, command, hover: bool = True) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=CHIP,
            fg=TEXT,
            activebackground=CHIP_HI,
            activeforeground=TEXT,
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            font=("JetBrains Mono", 9, "bold"),
            padx=10,
            pady=5,
        )
        if hover:
            self._bind_hover(btn, CHIP, CHIP_HI)
        return btn

    def _primary_button(self, parent: tk.Misc, text: str, command, padx: int = 12, pady: int = 6) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=CYAN,
            fg="#021018",
            activebackground=self._hover_color(CYAN),
            activeforeground="#021018",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("JetBrains Mono", 9, "bold"),
            padx=padx,
            pady=pady,
        )
        self._bind_hover(btn, CYAN, self._hover_color(CYAN))
        return btn

    def _card(self, parent: tk.Misc, title: str, subtitle: str = "") -> tuple[tk.Frame, tk.Frame]:
        card = tk.Frame(parent, bg=PANEL_2, highlightbackground=STROKE, highlightthickness=1, bd=0)
        head = tk.Frame(card, bg=PANEL_2)
        head.pack(fill="x", padx=10, pady=(7, 3))
        tk.Label(head, text=title.upper(), bg=PANEL_2, fg=TEXT, font=("JetBrains Mono", 9, "bold")).pack(side="left")
        if subtitle:
            tk.Label(head, text=subtitle, bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 8)).pack(side="right", padx=(8, 0))
        tk.Frame(card, bg=STROKE, height=1).pack(fill="x", padx=10)
        body = tk.Frame(card, bg=PANEL_2)
        body.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        return card, body

    def _chip(self, parent: tk.Misc, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=CHIP,
            fg=TEXT,
            activebackground=CHIP_HI,
            activeforeground=TEXT,
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            font=("JetBrains Mono", 10, "bold"),
            padx=12,
            pady=8,
        )

    def _post_build(self) -> None:
        self._style_ttk()
        self._refresh_voice_button()
        self._refresh_auto_start_button()
        self._refresh_gfx_button()
        self._refresh_approvals()
        self._refresh_app_launcher()
        self._bind_hotkeys()
        self._refresh_job_status()

    def _style_ttk(self) -> None:
        try:
            style = ttk.Style(self.root)
            style.theme_use("clam")
            style.configure(
                "Jarvis.Horizontal.TProgressbar",
                troughcolor=TERM,
                background=CYAN,
                bordercolor=STROKE,
                lightcolor=CYAN,
                darkcolor=CYAN,
            )
            style.configure(
                "Jarvis.Vertical.TScrollbar",
                troughcolor=BG,
                background=CHIP,
                bordercolor=STROKE,
                arrowcolor=TEXT,
                lightcolor=CHIP,
                darkcolor=CHIP,
                gripcount=0,
                relief="flat",
            )
            style.map("Jarvis.Vertical.TScrollbar", background=[("active", CHIP_HI)])
            style.configure(
                "Jarvis.Horizontal.TScrollbar",
                troughcolor=BG,
                background=CHIP,
                bordercolor=STROKE,
                arrowcolor=TEXT,
                lightcolor=CHIP,
                darkcolor=CHIP,
                gripcount=0,
                relief="flat",
            )
            style.map("Jarvis.Horizontal.TScrollbar", background=[("active", CHIP_HI)])
            if getattr(self, "job_progress", None) is not None:
                self.job_progress.configure(style="Jarvis.Horizontal.TProgressbar")
        except Exception:
            pass

    def _rebuild_ui(self) -> None:
        try:
            geo = self.root.geometry()
        except Exception:
            geo = None
        for widget in (getattr(self, "_top_frame", None), getattr(self, "body", None)):
            if widget is not None:
                try:
                    widget.destroy()
                except Exception:
                    pass
        self._build_ui()
        self._post_build()
        if geo:
            try:
                self.root.geometry(geo)
            except Exception:
                pass

    def _cycle_theme(self) -> None:
        idx = THEME_ORDER.index(_current_theme)
        self._theme = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        _apply_palette(self._theme)
        self._rebuild_ui()
        self._save_prefs()

    def _toggle_gfx(self) -> None:
        self._gfx_full = not self._gfx_full
        self._refresh_gfx_button()
        self._reset_scene()
        self._save_prefs()

    def _refresh_gfx_button(self) -> None:
        if getattr(self, "gfx_button", None) is not None:
            self.gfx_button.configure(
                text="GFX FULL" if self._gfx_full else "GFX LITE",
                bg=OK if self._gfx_full else CHIP,
                fg=GREEN if self._gfx_full else TEXT,
            )

    def _on_hub_event(self, topic: str, payload: dict[str, Any]) -> None:
        if topic in {"command_start", "command_done", "agent_step", "approval", "scheduler_run"}:
            self._events.put(("hub_event", (topic, payload)))

    GRADIENTS: list[tuple[str, str, str]] = [
        ("aurora", "#0b152b", "#0a1e3d"),
        ("sunset", "#2b1b3d", "#0f2a3d"),
        ("mono", "#0a0a12", "#151526"),
        ("ocean", "#061a2e", "#0e3a4a"),
    ]

    def _blend(self, c1: str, c2: str, t: float) -> str:
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        return f"#{int(r1 + (r2 - r1) * t):02x}{int(g1 + (g2 - g1) * t):02x}{int(b1 + (b2 - b1) * t):02x}"

    def _draw_background(self) -> None:
        mode = self._bg.get("mode", "aurora")
        w = max(1, self.scene.winfo_width())
        h = max(1, self.scene.winfo_height())
        if mode == "gradient":
            idx = int(self._bg.get("value", 0) or 0)
            _name, top, bottom = self.GRADIENTS[idx % len(self.GRADIENTS)]
            steps = max(2, min(80, h // 6))
            for i in range(steps):
                t = i / (steps - 1)
                y0 = int(i * h / steps)
                y1 = int((i + 1) * h / steps)
                self.scene.create_rectangle(0, y0, w, y1, fill=self._blend(top, bottom, t), outline="")
        elif mode == "image":
            self._bg_photo = None
            name = str(self._bg.get("value") or "")
            path = background_image_path(_backgrounds_dir(), name) if name else None
            if path is not None:
                try:
                    photo = tk.PhotoImage(file=str(path))
                    scale = max(1, photo.width() // max(1, w))
                    if scale > 1:
                        photo = photo.subsample(scale, scale)
                    self._bg_photo = photo
                    self.scene.create_image(0, 0, anchor="nw", image=photo)
                    return
                except Exception:
                    pass
            self.scene.create_rectangle(0, 0, w, h, fill=SCENE, outline="")
        else:
            for i, c in enumerate(["#050a14", "#081020", "#0b1629", "#0e1b33"]):
                y0 = int((i / 4) * h)
                y1 = int(((i + 1) / 4) * h)
                self.scene.create_rectangle(0, y0, w, y1, fill=c, outline="")

    def _apply_background(self, mode: str, value: str = "") -> None:
        self._bg = {"mode": mode, "value": value}
        self._reset_scene()
        self._save_prefs()

    def _open_backgrounds(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 Backgrounds")
        win.configure(bg=BG)
        win.geometry("640x420")
        win.transient(self.root)

        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(head, text="BACKGROUND", bg=BG, fg=TEXT, font=("JetBrains Mono", 13, "bold")).pack(side="left")

        mode_row = tk.Frame(win, bg=BG)
        mode_row.pack(fill="x", padx=14)
        current_mode = self._bg.get("mode", "aurora")
        for name, label in [("aurora", "Aurora scene"), ("gradient", "Gradient"), ("image", "Static image")]:
            btn = self._chip(mode_row, label, lambda m=name: self._apply_background(m))
            btn.configure(bg=OK if current_mode == name else CHIP, fg=GREEN if current_mode == name else TEXT)
            btn.pack(side="left", padx=(0, 8))

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(body, text="", bg=BG, fg=MUTED, font=("JetBrains Mono", 8)).pack(anchor="w")
        if current_mode == "gradient":
            grid = tk.Frame(body, bg=BG)
            grid.pack(anchor="w")
            for i, (_name, top, bottom) in enumerate(self.GRADIENTS):
                sw = tk.Canvas(grid, width=120, height=60, highlightthickness=1, highlightbackground=STROKE, cursor="hand2")
                sw.grid(row=i // 3, column=i % 3, padx=6, pady=6)
                for step in range(24):
                    sw.create_rectangle(0, int(step * 60 / 24), 120, int((step + 1) * 60 / 24),
                                        fill=self._blend(top, bottom, step / 23), outline="")

                def _pick_gradient(idx: int) -> None:
                    self._apply_background("gradient", str(idx))

                def _make_gradient_binding(idx: int) -> Callable[[Any], None]:
                    return lambda _e: _pick_gradient(idx)

                sw.bind("<Button-1>", _make_gradient_binding(i))
        elif current_mode == "image":
            tk.Button(
                body, text="BROWSE\u2026", command=lambda: self._pick_background_image(win),
                bg=CYAN, fg="#021018", relief=tk.FLAT, bd=0, padx=12, pady=5,
                font=("JetBrains Mono", 9, "bold"), cursor="hand2",
            ).pack(anchor="w", pady=(0, 8))
            files = list_background_images(_backgrounds_dir())
            grid = tk.Frame(body, bg=BG)
            grid.pack(anchor="w")
            if not files:
                tk.Label(body, text="No images yet \u2014 pick one above, or drop a .png/.gif in data/backgrounds/.",
                         bg=BG, fg=MUTED, font=("JetBrains Mono", 8)).pack(anchor="w", pady=6)
            for idx, item in enumerate(files):
                btn = self._chip(grid, item["name"], lambda n=item["name"]: self._apply_background("image", n))
                btn.configure(bg=OK if self._bg.get("value") == item["name"] else CHIP,
                              fg=GREEN if self._bg.get("value") == item["name"] else TEXT)
                btn.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0, 8), pady=4)
            tk.Button(body, text="REFRESH", command=lambda: self._reopen_backgrounds(win), bg=BTN2, fg=TEXT,
                      relief=tk.FLAT, bd=0, padx=10, pady=5, font=("JetBrains Mono", 9, "bold"), cursor="hand2").pack(anchor="w", pady=8)
        else:
            tk.Label(body, text="Animated aurora scene with drifting particles. Toggle GFX for more/fewer particles.",
                     bg=BG, fg=MUTED, font=("JetBrains Mono", 8), justify="left", wraplength=560).pack(anchor="w", pady=8)

        actions = tk.Frame(win, bg=BG)
        actions.pack(fill="x", padx=14, pady=(0, 14))
        tk.Button(actions, text="DONE", command=win.destroy, bg=CYAN, fg="#021018", relief=tk.FLAT, bd=0,
                  padx=14, pady=6, font=("JetBrains Mono", 9, "bold"), cursor="hand2").pack(side="right")

    def _reopen_backgrounds(self, win: tk.Toplevel) -> None:
        try:
            win.destroy()
        except Exception:
            pass
        self._open_backgrounds()

    def _pick_background_image(self, win: tk.Toplevel) -> None:
        """Open a native file dialog, import the chosen image into data/backgrounds, and apply it."""
        path = filedialog.askopenfilename(
            title="Choose a background image",
            parent=win,
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        src = Path(path)
        try:
            dest_dir = _backgrounds_dir()
            dest_dir.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() in {".png", ".gif"}:
                dest = _unique_path(dest_dir, src.name)
                shutil.copy2(src, dest)
            else:
                from PIL import Image

                img = Image.open(src)
                img.load()
                dest = _unique_path(dest_dir, src.stem + ".png")
                img.convert("RGBA").save(dest, "PNG")
            self._apply_background("image", dest.name)
            self._reopen_backgrounds(win)
        except Exception:
            self._toaster.notify("Background import failed", "Could not import that image.", RED)

    def _build_ui(self) -> None:
        self._scene_hidden = False
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.root.option_add("*Menu*background", PANEL_2)
        self.root.option_add("*Menu*foreground", TEXT)
        self.root.option_add("*Menu*activeBackground", SEL)
        self.root.option_add("*Menu*activeForeground", "#ffffff")
        self.root.option_add("*Menu*font", ("JetBrains Mono", 9))

        # ------------------------------------------------------------- header
        top = tk.Frame(self.root, bg=BG)
        self._top_frame = top
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 10))
        top.columnconfigure(1, weight=1)

        brand = tk.Frame(top, bg=BG)
        brand.grid(row=0, column=0, sticky="w")
        tk.Label(brand, text="JARVIS", bg=BG, fg=TEXT, font=("JetBrains Mono", 20, "bold")).pack(side="left")
        self.online_label = tk.Label(
            brand, text="STATUS: ONLINE", bg=STATUS, fg=GREEN, font=("JetBrains Mono", 9, "bold"), padx=10, pady=4
        )
        self.online_label.pack(side="left", padx=(12, 0))

        bar = tk.Frame(top, bg=BG)
        bar.grid(row=0, column=2, sticky="e")

        def _group() -> tk.Frame:
            g = tk.Frame(bar, bg=BG)
            g.pack(side="left", padx=(0, 8))
            return g

        def _divider() -> None:
            tk.Frame(bar, bg=STROKE, width=1).pack(side="left", padx=2, fill="y")

        transport = _group()
        for txt, cmd_text, tip in [
            ("▶", "youtube pause", "Toggle YouTube play/pause"),
            ("⏭", "youtube next", "Go to next YouTube video"),
            ("APPS", "list apps", "Show apps in the launcher panel"),
        ]:
            b = self._header_chip(transport, txt, lambda c=cmd_text: self._run_preset(c))
            b.pack(side="left", padx=(0, 4))
            self._tooltips.append(Tooltip(b, tip))

        _divider()
        primary = _group()
        for txt, cmd_fn, tip in [
            ("MINI", self.mini.toggle, "Show/hide the floating mini HUD"),
            ("DIAGNOSE", self._diagnose_system, "Run full system diagnosis (CPU/RAM/GPU + process analysis)"),
            ("RECAP", self._recap, "What happened while you were away"),
        ]:
            b = self._header_chip(primary, txt, cmd_fn)
            b.pack(side="left", padx=(0, 4))
            self._tooltips.append(Tooltip(b, tip))

        _divider()
        toggles = _group()
        self.auto_start_button = self._header_chip(toggles, "AUTOSTART", self._toggle_auto_start, hover=False)
        self.auto_start_button.pack(side="left", padx=(0, 4))
        self._tooltips.append(Tooltip(self.auto_start_button, "Toggle auto-start when this project opens / Windows login"))
        self.tray_button = self._header_chip(toggles, "TRAY", self._toggle_tray, hover=False)
        self.tray_button.pack(side="left", padx=(0, 4))
        self._tooltips.append(Tooltip(self.tray_button, "Minimize to system tray  /  Ctrl+Shift+J summons the mini HUD"))
        self.gfx_button = self._header_chip(toggles, "GFX", self._toggle_gfx, hover=False)
        self.gfx_button.pack(side="left", padx=(0, 4))
        self._tooltips.append(Tooltip(self.gfx_button, "Toggle Lite / Full graphics effects"))
        self.theme_button = self._header_chip(toggles, f"THEME {self._theme.upper()}", self._cycle_theme, hover=False)
        self.theme_button.pack(side="left", padx=(0, 4))
        self._tooltips.append(Tooltip(self.theme_button, "Cycle Aurora / Cyber / Light themes"))

        _divider()
        more_menu = tk.Menu(top, tearoff=0)
        more_menu.add_command(label="HELP  (F1)", command=self._open_help)
        more_menu.add_command(label="SCHEDULER", command=self._open_scheduler)
        more_menu.add_command(label="HISTORY", command=self._open_history)
        more_menu.add_command(label="TASKS", command=self._open_tasks)
        more_menu.add_command(label="MEMORY", command=self._open_memory)
        more_menu.add_separator()
        more_menu.add_command(label="BACKGROUNDS", command=self._open_backgrounds)

        def _show_more() -> None:
            try:
                x = more_btn.winfo_rootx()
                y = more_btn.winfo_rooty() + more_btn.winfo_height()
                more_menu.tk_popup(x, y)
            finally:
                more_menu.grab_release()

        more_btn = self._header_chip(bar, "MORE ▾", _show_more)
        more_btn.pack(side="left", padx=(0, 4))
        self._tooltips.append(Tooltip(more_btn, "Scheduler, history, tasks, memory, backgrounds and help"))

        self.body = tk.Frame(self.root, bg=BG)
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 14))
        self.body.columnconfigure(0, weight=5)
        self.body.columnconfigure(1, weight=4)
        self.body.columnconfigure(2, weight=4)
        self.body.rowconfigure(0, weight=1)
        self.body.bind("<Configure>", self._on_body_resize)

        left = self._make_panel(self.body, PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        # ------------------------------------------------------- command center
        cmd_card, cmd_body = self._card(left, "Command Center", "Natural-language control")
        cmd_body.columnconfigure(0, weight=1)
        self.entry_wrap = tk.Frame(cmd_body, bg=SEL, padx=1, pady=1)
        self.entry_wrap.grid(row=0, column=0, sticky="ew")
        self.command_entry = tk.Entry(
            self.entry_wrap, bg=ENTRY, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 12)
        )
        self.command_entry.pack(fill="x", ipady=7)
        self.command_entry.bind("<Return>", lambda _: self._run_command())
        self.command_entry.bind("<Up>", lambda _: self._history_step(-1))
        self.command_entry.bind("<Down>", lambda _: self._history_step(1))
        self.run_button = self._primary_button(cmd_body, "EXECUTE", self._run_command)
        self.run_button.grid(row=0, column=1, padx=(10, 0))
        chips_row = tk.Frame(cmd_body, bg=PANEL_2)
        chips_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for label, cmd_str in [
            ("system status", "system status"),
            ("metrics", "metrics"),
            ("list schedules", "list schedules"),
            ("job status", "job status"),
            ("time", "get current time"),
        ]:
            c = self._header_chip(chips_row, label, lambda c=cmd_str: self._run_preset(c))
            c.pack(side="left", padx=(0, 6))
        cmd_card.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))

        # ---------------------------------------------------------- activity feed
        feed_card, feed_body = self._card(left, "Activity Feed", "Commands, agents and scheduler events")
        feed_head = tk.Frame(feed_body, bg=PANEL_2)
        feed_head.pack(fill="x", pady=(0, 4))
        feed_head.columnconfigure(0, weight=1)
        tk.Button(
            feed_head, text="CLEAR", command=self._clear_feed, bg=BTN2, fg=TEXT, activebackground=CHIP_HI,
            relief=tk.FLAT, bd=0, padx=8, pady=3, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=1, sticky="e")
        feed_scroll = tk.Frame(feed_body, bg=TERM, highlightbackground=STROKE, highlightthickness=1)
        feed_scroll.pack(fill="both", expand=True)
        feed_scroll.columnconfigure(0, weight=1)
        feed_scroll.rowconfigure(0, weight=1)
        self.feed_text = tk.Text(
            feed_scroll, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, width=46, height=7, font=("JetBrains Mono", 9),
            wrap="word", padx=8, pady=6,
        )
        self.feed_text.grid(row=0, column=0, sticky="nsew")
        feed_sb = ttk.Scrollbar(feed_scroll, orient="vertical", command=self.feed_text.yview, style="Jarvis.Vertical.TScrollbar")
        feed_sb.grid(row=0, column=1, sticky="ns")
        self.feed_text.configure(yscrollcommand=feed_sb.set)
        self.feed_text.configure(state=tk.DISABLED)
        for tag, color in [("feed_cmd", CYAN), ("feed_ok", GREEN), ("feed_pending", AMBER), ("feed_err", RED), ("feed_sys", MUTED)]:
            self.feed_text.tag_configure(tag, foreground=color)
        feed_card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        # ----------------------------------------------------------- live terminal
        term_card, term_body = self._card(left, "Live Terminal", "Command, agent and scheduler logs")
        term_head = tk.Frame(term_body, bg=PANEL_2)
        term_head.pack(fill="x", pady=(0, 4))
        term_head.columnconfigure(0, weight=1)
        tk.Label(term_head, text="ALL EVENTS STREAM HERE", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 8)).grid(
            row=0, column=0, sticky="w"
        )
        tk.Button(
            term_head, text="CLEAR", command=self._clear_terminal, bg=BTN2, fg=TEXT, activebackground=CHIP_HI,
            relief=tk.FLAT, bd=0, padx=8, pady=3, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=1, sticky="e")
        term_scroll = tk.Frame(term_body, bg=TERM, highlightbackground=STROKE, highlightthickness=1)
        term_scroll.pack(fill="both", expand=True)
        term_scroll.columnconfigure(0, weight=1)
        term_scroll.rowconfigure(0, weight=1)
        self.terminal = tk.Text(
            term_scroll, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, width=60, wrap="word", font=("JetBrains Mono", 10), padx=10, pady=8
        )
        self.terminal.grid(row=0, column=0, sticky="nsew")
        term_sb = ttk.Scrollbar(term_scroll, orient="vertical", command=self.terminal.yview, style="Jarvis.Vertical.TScrollbar")
        term_sb.grid(row=0, column=1, sticky="ns")
        self.terminal.configure(yscrollcommand=term_sb.set)
        self.terminal.configure(state=tk.DISABLED)
        for tag, color in [("ts", MUTED), ("cmd", CYAN), ("ok", GREEN), ("pending", AMBER), ("err", RED), ("sys", INDIGO)]:
            self.terminal.tag_configure(tag, foreground=color)
        term_card.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # ------------------------------------------------------------ AI assistant
        mid = self._make_panel(self.body, PANEL)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        ai_card, ai_body = self._card(mid, "AI Assistant", "Jarvis plans, executes and streams answers")
        ai_card.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        ai_body.columnconfigure(0, weight=1)
        ai_body.rowconfigure(0, weight=1)
        chat_scroll = tk.Frame(ai_body, bg=CANVAS, highlightbackground=STROKE, highlightthickness=1)
        chat_scroll.grid(row=0, column=0, sticky="nsew")
        chat_scroll.columnconfigure(0, weight=1)
        chat_scroll.rowconfigure(0, weight=1)
        self.chat_text = tk.Text(
            chat_scroll, bg=CANVAS, fg=TEXT, relief=tk.FLAT, bd=0, width=38, wrap="word", font=("Fira Code", 10), padx=12, pady=10
        )
        self.chat_text.grid(row=0, column=0, sticky="nsew")
        chat_sb = ttk.Scrollbar(chat_scroll, orient="vertical", command=self.chat_text.yview, style="Jarvis.Vertical.TScrollbar")
        chat_sb.grid(row=0, column=1, sticky="ns")
        self.chat_text.configure(yscrollcommand=chat_sb.set)
        self.chat_text.configure(state=tk.DISABLED)
        self.chat_text.tag_configure("chat_user_label", foreground=CYAN, font=("JetBrains Mono", 8, "bold"))
        self.chat_text.tag_configure("chat_user", foreground=TEXT)
        self.chat_text.tag_configure("chat_ai_label", foreground=INDIGO, font=("JetBrains Mono", 8, "bold"))
        self.chat_text.tag_configure("chat_ai", foreground=TEXT)
        self.chat_text.tag_configure("chat_stream", foreground=TEXT)
        inp = tk.Frame(ai_body, bg=PANEL_2)
        inp.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        inp.columnconfigure(0, weight=1)
        shell = tk.Frame(inp, bg=SHELL, padx=1, pady=1)
        shell.grid(row=0, column=0, sticky="ew")
        shell.columnconfigure(0, weight=1)
        self.chat_entry = tk.Entry(shell, bg=CANVAS, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 11))
        self.chat_entry.grid(row=0, column=0, sticky="ew", ipady=8)
        self.chat_entry.bind("<Return>", lambda _: self._ask_chatbot())
        ask_btn = self._primary_button(inp, "ASK", self._ask_chatbot)
        ask_btn.grid(row=0, column=1, padx=(10, 0))

        # ----------------------------------------------------------- right column
        right = self._make_panel(self.body, PANEL)
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        right_canvas = tk.Canvas(right, bg=PANEL, highlightthickness=0, bd=0)
        right_canvas.grid(row=0, column=0, sticky="nsew")
        right_sb = ttk.Scrollbar(right, orient="vertical", command=right_canvas.yview, style="Jarvis.Vertical.TScrollbar")
        right_sb.grid(row=0, column=1, sticky="ns")
        right_canvas.configure(yscrollcommand=right_sb.set)
        self.right_inner = tk.Frame(right_canvas, bg=PANEL)
        self.right_window_id = right_canvas.create_window((0, 0), window=self.right_inner, anchor="nw")
        self.right_inner.bind("<Configure>", lambda _e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>", lambda e: right_canvas.itemconfigure(self.right_window_id, width=e.width))
        self.right_inner.columnconfigure(0, weight=1)

        # -------------------------------------------------------------- job agent
        job_card, job_body = self._card(self.right_inner, "Job Agent", "Autonomous job-application hunter")
        job_body.columnconfigure(0, weight=1)
        self.job_status_label = tk.Label(
            job_body, text="No job-application task running.", bg=INNER, fg=MUTED,
            font=("JetBrains Mono", 9), justify="left", anchor="w", wraplength=320, padx=8, pady=4,
        )
        self.job_status_label.grid(row=0, column=0, sticky="ew")
        job_stats = tk.Frame(job_body, bg=PANEL_2)
        job_stats.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.job_stat_labels: dict[str, tk.Label] = {}
        stat_colors = {
            "APPLIED": GREEN, "SKIPPED": AMBER, "FAILED": RED,
            "INTERVIEW": CYAN, "OFFER": GREEN, "REJECTED": RED,
        }
        for row_i, names in enumerate([("APPLIED", "SKIPPED", "FAILED"), ("INTERVIEW", "OFFER", "REJECTED")]):
            for i, name in enumerate(names):
                lbl = tk.Label(job_stats, text=f"{name} 0", bg=PANEL_2, fg=stat_colors[name], font=("JetBrains Mono", 9, "bold"))
                lbl.grid(row=row_i, column=i, padx=(0, 14), sticky="w")
                self.job_stat_labels[name] = lbl
        self.job_progress = ttk.Progressbar(job_body, maximum=100, mode="determinate")
        self.job_progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        job_ctrl = tk.Frame(job_body, bg=PANEL_2)
        job_ctrl.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        job_ctrl.columnconfigure(0, weight=1)
        shell2 = tk.Frame(job_ctrl, bg=SHELL, padx=1, pady=1)
        shell2.grid(row=0, column=0, sticky="ew")
        shell2.columnconfigure(0, weight=1)
        self.job_query_entry = tk.Entry(shell2, bg=CANVAS, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9))
        self.job_query_entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self.job_query_entry.bind("<Return>", lambda _: self._start_job_apply())
        apply_btn = self._primary_button(job_ctrl, "APPLY", self._start_job_apply, padx=10)
        apply_btn.grid(row=0, column=1, padx=(8, 0))
        ctrl2 = tk.Frame(job_body, bg=PANEL_2)
        ctrl2.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        for txt, cmd_fn, kind in [
            ("STATUS", self._refresh_job_status, "secondary"),
            ("CANCEL", self._cancel_job, "danger"),
            ("RESUME", self._resume_job, "success"),
        ]:
            if kind == "danger":
                b = tk.Button(ctrl2, text=txt, command=cmd_fn, bg=DANGER, fg=RED, activebackground=CHIP_HI, relief=tk.FLAT, bd=0,
                              padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2")
            elif kind == "success":
                b = tk.Button(ctrl2, text=txt, command=cmd_fn, bg=OK, fg=GREEN, activebackground=CHIP_HI, relief=tk.FLAT, bd=0,
                              padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2")
            else:
                b = tk.Button(ctrl2, text=txt, command=cmd_fn, bg=BTN2, fg=TEXT, activebackground=CHIP_HI, relief=tk.FLAT, bd=0,
                              padx=8, pady=4, font=("JetBrains Mono", 8, "bold"), cursor="hand2")
                self._bind_hover(b, BTN2, CHIP_HI)
            b.pack(side="left", padx=(0, 6))
        tk.Label(job_body, text="RECENT APPLICATIONS", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 8, "bold")).grid(
            row=5, column=0, sticky="w", pady=(8, 2)
        )
        self.recent_list = tk.Listbox(
            job_body, bg=CANVAS, fg=TEXT, relief=tk.FLAT, bd=0, height=4, font=("JetBrains Mono", 9),
            highlightthickness=1, highlightbackground=STROKE, selectbackground=SEL, selectforeground=TEXT,
        )
        self.recent_list.grid(row=6, column=0, sticky="ew")
        job_card.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))

        # ------------------------------------------------------------ voice engine
        voice_card, voice_body = self._card(self.right_inner, "Voice Engine", "Click the wave to toggle")
        voice_body.columnconfigure(0, weight=1)
        wave_wrap = tk.Frame(voice_body, bg=INNER, highlightbackground=STROKE, highlightthickness=1)
        wave_wrap.grid(row=0, column=0, sticky="ew")
        self.wave_canvas = tk.Canvas(wave_wrap, width=320, height=64, bg=CANVAS, highlightthickness=0, bd=0, cursor="hand2")
        self.wave_canvas.pack(fill="both", expand=True, padx=4, pady=4)
        self.wave_canvas.bind("<Button-1>", lambda _: self._toggle_voice())
        self.voice_switch_button = tk.Button(
            voice_body, text="VOICE OFF", command=self._toggle_voice, bg=BTN2, fg=TEXT, activebackground=CHIP_HI,
            relief=tk.FLAT, bd=0, padx=10, pady=5, font=("JetBrains Mono", 9, "bold"), cursor="hand2",
        )
        self.voice_switch_button.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        voice_card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        # --------------------------------------------------------- agentive status
        agent_card, agent_body = self._card(self.right_inner, "Agent Status", "Pending approval requests")
        agent_body.columnconfigure(0, weight=1)
        agent_body.rowconfigure(0, weight=1)
        agent_head = tk.Frame(agent_body, bg=PANEL_2)
        agent_head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        agent_head.columnconfigure(0, weight=1)
        tk.Label(agent_head, text="PENDING APPROVALS", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 8, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._approval_badge = tk.Label(agent_head, text="0", bg=BTN2, fg=TEXT, font=("JetBrains Mono", 8, "bold"), padx=6, pady=1)
        self._approval_badge.grid(row=0, column=1, sticky="e")
        approval_shell = tk.Frame(agent_body, bg=CANVAS, highlightbackground=STROKE, highlightthickness=1)
        approval_shell.grid(row=1, column=0, sticky="nsew")
        approval_shell.columnconfigure(0, weight=1)
        approval_shell.rowconfigure(0, weight=1)
        self.approval_canvas = tk.Canvas(approval_shell, bg=CANVAS, width=320, height=120, highlightthickness=0, bd=0)
        self.approval_canvas.grid(row=0, column=0, sticky="nsew")
        approval_sb = ttk.Scrollbar(approval_shell, orient="vertical", command=self.approval_canvas.yview, style="Jarvis.Vertical.TScrollbar")
        approval_sb.grid(row=0, column=1, sticky="ns")
        self.approval_canvas.configure(yscrollcommand=approval_sb.set)
        self.approval_inner = tk.Frame(self.approval_canvas, bg=CANVAS)
        self.approval_window_id = self.approval_canvas.create_window((0, 0), window=self.approval_inner, anchor="nw")
        self.approval_inner.bind("<Configure>", lambda _e: self.approval_canvas.configure(scrollregion=self.approval_canvas.bbox("all")))
        agent_card.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        # ---------------------------------------------------------- available apps
        apps_card, apps_body = self._card(self.right_inner, "Available Apps", "Alt+1..9 to launch")
        apps_body.columnconfigure(0, weight=1)
        self.app_filter_var = tk.StringVar()
        self.app_filter_var.trace_add("write", lambda *_: self._apply_app_filter())
        filt = tk.Frame(apps_body, bg=SHELL, padx=1, pady=1)
        filt.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filt.columnconfigure(0, weight=1)
        tk.Entry(filt, textvariable=self.app_filter_var, bg=CANVAS, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0,
                 font=("JetBrains Mono", 9)).grid(row=0, column=0, sticky="ew", ipady=4)
        self.app_listbox = tk.Listbox(
            apps_body, bg=LIST, fg=TEXT, relief=tk.FLAT, bd=0, height=5, font=("JetBrains Mono", 9),
            highlightthickness=1, highlightbackground=STROKE, selectbackground=SEL, selectforeground=TEXT,
        )
        self.app_listbox.grid(row=1, column=0, sticky="nsew")
        self.app_listbox.bind("<Double-Button-1>", lambda _: self._open_selected_app())
        self.app_listbox.bind("<Return>", lambda _: self._open_selected_app())
        apps_card.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))

        # ------------------------------------------------------------- scene viewer
        scene_card, scene_body = self._card(self.right_inner, "Scene Viewer", "Aurora · Gradient · Image")
        scene_body.columnconfigure(0, weight=1)
        self.scene = tk.Canvas(scene_body, bg=SCENE, highlightthickness=0, bd=0, width=1, height=120)
        self.scene.grid(row=0, column=0, sticky="nsew")
        self.scene.bind("<Configure>", self._reset_scene)
        scene_card.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _on_body_resize(self, event: tk.Event) -> None:
        scene = getattr(self, "scene", None)
        if scene is None:
            return
        w = int(event.width)
        hidden = getattr(self, "_scene_hidden", False)
        if w >= 1560:
            self.body.columnconfigure(0, weight=5)
            self.body.columnconfigure(1, weight=4)
            self.body.columnconfigure(2, weight=4)
            if hidden:
                scene.grid()
                self._scene_hidden = False
        elif w >= 1360:
            self.body.columnconfigure(0, weight=6)
            self.body.columnconfigure(1, weight=5)
            self.body.columnconfigure(2, weight=4)
            if hidden:
                scene.grid()
                self._scene_hidden = False
        else:
            self.body.columnconfigure(0, weight=6)
            self.body.columnconfigure(1, weight=5)
            self.body.columnconfigure(2, weight=3)
            if not hidden:
                scene.grid_remove()
                self._scene_hidden = True

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
                result = self.runtime.orchestrator.execute_command(
                    command=command,
                    source="gui",
                    auto_approve=False,
                    on_step=lambda step: self._events.put(("agent_step", step)),
                )
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
                try:
                    recent = self.runtime.registry.execute("job_applied", {"limit": 12})
                    payload["recent"] = recent.get("items", [])
                except Exception:
                    pass
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

    def _resume_job(self) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("job_resume", {})
                self._events.put(("job_status", payload))
                self._events.put(("job_msg", payload.get("result_text", "Job task resumed.")))
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
            self._theme = str(cfg.get("theme") or "aurora")
            self._gfx_full = bool(cfg.get("gfx_full", False))
            bg = cfg.get("background") or {}
            if isinstance(bg, dict) and bg.get("mode") in {"aurora", "gradient", "image"}:
                self._bg = bg
            self._cmd_history = [str(x) for x in (cfg.get("cmd_history") or [])][:50]
            if self._theme not in PALETTES:
                self._theme = "aurora"
            if self._theme != "aurora":
                _apply_palette(self._theme)
                self._rebuild_ui()
            else:
                _apply_palette("aurora")
            try:
                self._reset_scene()
            except Exception:
                pass
        except Exception:
            pass

    def _save_ui_config(self) -> None:
        try:
            cfg = {
                "main_geometry": self.root.geometry(),
                "mini_visible": self.mini.is_visible(),
                "theme": self._theme,
                "gfx_full": self._gfx_full,
                "background": self._bg,
                "cmd_history": self._cmd_history[:50],
            }
            self._ui_config_path.parent.mkdir(parents=True, exist_ok=True)
            self._ui_config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _save_prefs(self) -> None:
        self._save_ui_config()

    def _maybe_show_recap(self) -> None:
        state = self.runtime.memory.get_state("session_review", default={})
        last_seen = state.get("last_seen_at")
        self.runtime.memory.set_state("session_review", {"last_seen_at": datetime.now(UTC).isoformat()})
        if not last_seen:
            return
        try:
            review = self.runtime.memory.session_review(since=last_seen)
        except Exception as exc:
            self._append_terminal("ERR", f"Recap failed: {exc}")
            return
        if not review["command_count"] and not review["job_applications"] and not review["scheduled_runs"]:
            return
        self._open_recap(review, auto=True)

    def _recap(self) -> None:
        state = self.runtime.memory.get_state("session_review", default={})
        since = state.get("last_seen_at")
        try:
            review = self.runtime.memory.session_review(since=since)
        except Exception as exc:
            self._toaster.notify("Recap error", str(exc), RED)
            return
        self._open_recap(review)

    def _open_recap(self, review: dict[str, Any], auto: bool = False) -> None:
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 While you were away")
        win.configure(bg=BG)
        win.geometry("760x600")
        win.transient(self.root)
        text = tk.Text(win, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, wrap="word", font=("JetBrains Mono", 9), padx=16, pady=10)
        scroll = tk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.tag_configure("h", foreground=CYAN, font=("JetBrains Mono", 11, "bold"))
        text.tag_configure("d", foreground=TEXT)
        text.tag_configure("m", foreground=MUTED)

        lines: list[tuple[str, str]] = [
            ("h", "WHILE YOU WERE AWAY\n"),
            ("h", "\nCOMMANDS\n"),
        ]
        counts = review.get("sources") or {}
        if counts:
            lines.append(("m", "  " + "  \u00b7  ".join(f"{src}: {n}" for src, n in counts.items()) + "\n"))
        for row in (review.get("commands") or [])[-12:]:
            snippet = str(row.get("response_text") or "")[:120].replace("\n", " ")
            lines.append(("d", f"  {row.get('created_at', '')[:16]} {row.get('source')}: {row.get('command')}\n"))
            lines.append(("m", f"      {snippet}\n"))

        lines.append(("h", "\nTOOL ACTIVITY\n"))
        tool_counts = review.get("tool_counts") or {}
        if tool_counts:
            lines.append(("m", "  " + "  \u00b7  ".join(f"{k}: {v}" for k, v in tool_counts.items()) + "\n"))
        top_tools = review.get("top_tools") or {}
        if top_tools:
            lines.append(("d", "  Most used: " + ", ".join(top_tools) + "\n"))

        lines.append(("h", "\nJOB PIPELINE\n"))
        job_status = review.get("job_status") or {}
        if job_status:
            lines.append(("m", "  " + "  \u00b7  ".join(f"{k}: {v}" for k, v in job_status.items()) + "\n"))
        for row in (review.get("job_applications") or [])[-10:]:
            lines.append(("d", f"  [{row.get('status')}] {row.get('title')} @ {row.get('company') or row.get('site')}\n"))

        lines.append(("h", "\nSCHEDULED RUNS\n"))
        runs = review.get("scheduled_runs") or []
        for run in runs[-10:]:
            lines.append(("d", f"  [{run.get('status')}] {run.get('result', '')[:100]}\n"))

        if not lines:
            lines = [("m", "Nothing to report.")]

        text.configure(state=tk.NORMAL)
        for tag, chunk in lines:
            text.insert(tk.END, chunk, (tag,))
        text.configure(state=tk.DISABLED)

        if auto:
            def _open_recap() -> None:
                win.destroy()
                self._raise_main_window()

            self._toaster.notify("While you were away", "Click to open the catch-up recap.", CYAN, on_click=_open_recap)

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
            bg=CHIP if on else DANGER,
            fg=GREEN if on else AMBER,
        )

    def _toggle_tray(self) -> None:
        if not self._tray.available:
            self.root.bell()
            self._toaster.notify("Tray unavailable", "pystray is not installed.", RED)
            return
        if self.root.state() == "withdrawn":
            self._raise_main_window()
            return
        if not self._tray.start():
            self._raise_main_window()
            return
        self.tray_button.configure(text="TRAY ON", bg=OK, fg=GREEN)
        self._toaster.notify(
            "JARVIS in tray", "HUD is running in the tray.\nUse the JARVIS icon or Ctrl+Shift+J to bring it back.", GREEN
        )
        self.root.withdraw()

    def _tray_show(self) -> None:
        try:
            self.root.after(0, self._raise_main_window)
        except Exception:
            pass

    def _tray_mini(self) -> None:
        try:
            self.root.after(0, self.mini.show)
        except Exception:
            pass

    def _tray_exit(self) -> None:
        try:
            self.root.after(0, self._on_close)
        except Exception:
            pass

    def _open_help(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 What can I do?")
        win.configure(bg=BG)
        win.geometry("740x580")
        win.transient(self.root)
        text = tk.Text(win, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, wrap="word", font=("JetBrains Mono", 9), padx=16, pady=10)
        scroll = tk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.tag_configure("h", foreground=CYAN, font=("JetBrains Mono", 11, "bold"))
        text.tag_configure("d", foreground=TEXT)
        text.tag_configure("m", foreground=MUTED)

        entries: list[tuple[str, str]] = [
            ("h", "VOICE + DOCK\n"),
            ("d", "  youtube pause          toggle play/pause\n"),
            ("d", "  youtube next           next video\n"),
            ("d", "  list apps              show apps in the launcher\n"),
            ("d", "  open whatsapp chat <name>\n"),
            ("d", "  open <app or site>     e.g. 'open chrome', 'open google'\n"),
            ("d", "  search web <query>\n"),
            ("d", "  take screenshot\n"),
            ("d", "  play <song>            music via yt-dlp\n"),
            ("h", "\nJOB AGENT\n"),
            ("d", "  apply for <role> at <company>\n"),
            ("d", "  job status / job cancel\n"),
            ("h", f"\nTOOLS ({len(self.runtime.registry.catalog())})\n"),
        ]
        for tool in self.runtime.registry.catalog():
            entries.append(("d", f"  {tool.get('name', '')}\n"))
            entries.append(("m", f"      {tool.get('description', '')}  [risk: {tool.get('risk', 'low')}]\n"))
        for tag, chunk in entries:
            text.insert(tk.END, chunk, (tag,))
        text.configure(state=tk.DISABLED)

    def _clear_terminal(self) -> None:
        self.terminal.configure(state=tk.NORMAL)
        self.terminal.delete("1.0", tk.END)
        self.terminal.configure(state=tk.DISABLED)

    def _clear_feed(self) -> None:
        self.feed_text.configure(state=tk.NORMAL)
        self.feed_text.delete("1.0", tk.END)
        self.feed_text.configure(state=tk.DISABLED)

    def _open_scheduler(self) -> None:
        sched_win = getattr(self, "_sched_win", None)
        if sched_win is not None and sched_win.winfo_exists():
            sched_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 Scheduler")
        win.configure(bg=BG)
        win.geometry("760x520")
        win.transient(self.root)

        add_panel = self._make_panel(win, PANEL)
        add_panel.pack(fill="x", padx=14, pady=(14, 8))
        add_panel.columnconfigure(1, weight=1)
        add_panel.columnconfigure(3, weight=1)
        tk.Label(add_panel, text="NEW SCHEDULE", bg=PANEL, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2), columnspan=4
        )
        tk.Label(add_panel, text="command", bg=PANEL, fg=TEXT, font=("JetBrains Mono", 8)).grid(row=1, column=0, sticky="w", padx=10, pady=4)
        cmd_entry = tk.Entry(add_panel, bg=ENTRY, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9))
        cmd_entry.grid(row=1, column=1, sticky="ew", padx=6, ipady=5)
        tk.Label(add_panel, text="recurrence", bg=PANEL, fg=TEXT, font=("JetBrains Mono", 8)).grid(row=1, column=2, sticky="w", padx=6, pady=4)
        rec_entry = tk.Entry(add_panel, bg=ENTRY, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9))
        rec_entry.grid(row=1, column=3, sticky="ew", padx=6, ipady=5)
        tk.Button(
            add_panel, text="SCHEDULE", command=lambda: self._add_schedule(cmd_entry, rec_entry),
            bg=CYAN, fg="#021018", relief=tk.FLAT, bd=0, padx=12, pady=5,
            font=("JetBrains Mono", 9, "bold"), cursor="hand2",
        ).grid(row=1, column=4, padx=(8, 10), pady=4)
        tk.Label(add_panel, text="e.g. daily at 09:00  ·  hourly  ·  every 30 minutes  ·  weekly on monday at 10:00",
                 bg=PANEL, fg=MUTED, font=("JetBrains Mono", 8)).grid(row=2, column=0, columnspan=5, sticky="w", padx=10, pady=(0, 8))
        rec_entry.bind("<Return>", lambda _: self._add_schedule(cmd_entry, rec_entry))

        list_panel = self._make_panel(win, PANEL_2)
        list_panel.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        list_panel.rowconfigure(1, weight=1)
        list_panel.columnconfigure(0, weight=1)
        head = tk.Frame(list_panel, bg=PANEL_2)
        head.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="SCHEDULED JOBS", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Button(
            head, text="REFRESH", command=lambda: self._scheduler_load(), bg=BTN2, fg=TEXT,
            relief=tk.FLAT, bd=0, padx=8, pady=2, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=1, sticky="e")
        self._sched_listbox = tk.Listbox(list_panel, bg=LIST, fg=TEXT, relief=tk.FLAT, bd=0, font=("Consolas", 9), selectbackground=SEL)
        self._sched_listbox.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        sched_sb = tk.Scrollbar(list_panel, command=self._sched_listbox.yview, bg=PANEL, troughcolor=LIST, activebackground=STROKE)
        sched_sb.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        self._sched_listbox.configure(yscrollcommand=sched_sb.set)

        actions = tk.Frame(win, bg=BG)
        actions.pack(fill="x", padx=14, pady=(0, 14))
        self._sched_status = tk.Label(actions, text="", bg=BG, fg=MUTED, font=("JetBrains Mono", 8))
        self._sched_status.pack(side="left")
        tk.Button(
            actions, text="DELETE", command=self._delete_schedule, bg=DANGER, fg=RED,
            relief=tk.FLAT, bd=0, padx=10, pady=5, font=("JetBrains Mono", 9, "bold"), cursor="hand2",
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            actions, text="ENABLE / DISABLE", command=self._toggle_schedule, bg=BTN2, fg=TEXT,
            relief=tk.FLAT, bd=0, padx=10, pady=5, font=("JetBrains Mono", 9, "bold"), cursor="hand2",
        ).pack(side="right")
        self._sched_listbox.bind("<Double-Button-1>", lambda _: self._toggle_schedule())

        self._sched_win = win
        self._sched_jobs: list[dict[str, Any]] = []
        self._scheduler_load()
        self._scheduler_poll()

    def _scheduler_load(self) -> None:
        def worker() -> None:
            try:
                jobs = self.runtime.scheduler.list_jobs()
                self._events.put(("sched_list", jobs))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _scheduler_poll(self) -> None:
        alive = self.root.winfo_exists() and getattr(self, "_sched_win", None) is not None and self._sched_win.winfo_exists()
        if not alive:
            return
        self._scheduler_load()
        self.root.after(4000, self._scheduler_poll)

    def _handle_sched_list(self, jobs: list[dict[str, Any]]) -> None:
        if not getattr(self, "_sched_win", None) or not self._sched_win.winfo_exists():
            return
        self._sched_jobs = jobs
        box = self._sched_listbox
        box.delete(0, tk.END)
        if not jobs:
            box.insert(tk.END, "No schedules yet.")
        for job in jobs:
            state = "ON " if job.get("enabled") else "OFF"
            last = job.get("last_status") or "never"
            stamp = str(job.get("last_result") or "")[:60]
            box.insert(
                tk.END,
                f"[{state}] {job.get('id', '')}  {job.get('command', '')} \u2014 {job.get('recurrence', '')}  |  last: {last}{' · ' + stamp if stamp else ''}",
            )
        self._sched_status.configure(text=f"{len(jobs)} job(s) \u00b7 auto-refreshes every 4s")

    def _add_schedule(self, cmd_entry: tk.Entry, rec_entry: tk.Entry) -> None:
        command = cmd_entry.get().strip()
        recurrence = rec_entry.get().strip()
        if not command or not recurrence:
            self._sched_status.configure(text="Both command and recurrence are required.", fg=AMBER)
            return

        def worker() -> None:
            try:
                self.runtime.scheduler.add(command, recurrence)
                self._events.put(("sched_added", command))
            except ValueError as exc:
                self._events.put(("sched_error", str(exc)))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _delete_schedule(self) -> None:
        sel = self._sched_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._sched_jobs):
            return
        job_id = self._sched_jobs[idx].get("id")

        def worker() -> None:
            try:
                self.runtime.scheduler.remove(job_id)
                self._events.put(("sched_changed", "deleted"))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_schedule(self) -> None:
        sel = self._sched_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._sched_jobs):
            return
        job = self._sched_jobs[idx]
        job_id = job.get("id")
        enabled = bool(job.get("enabled"))

        def worker() -> None:
            try:
                self.runtime.scheduler.set_enabled(job_id, not enabled)
                self._events.put(("sched_changed", "toggled"))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _open_history(self) -> None:
        hist_win = getattr(self, "_hist_win", None)
        if hist_win is not None and hist_win.winfo_exists():
            hist_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 History & Insights")
        win.configure(bg=BG)
        win.geometry("820x560")
        win.transient(self.root)
        win.rowconfigure(1, weight=1)
        win.columnconfigure(0, weight=1)

        head = tk.Frame(win, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="HISTORY & INSIGHTS", bg=BG, fg=CYAN, font=("JetBrains Mono", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Button(
            head, text="REFRESH", command=self._history_load, bg=BTN2, fg=TEXT,
            relief=tk.FLAT, bd=0, padx=8, pady=3, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=1, sticky="e")

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Hist.TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("Hist.TNotebook.Tab", background=PANEL_2, foreground=TEXT, padding=(14, 7), font=("JetBrains Mono", 9, "bold"))
        style.map("Hist.TNotebook.Tab", background=[("selected", SEL)], foreground=[("selected", TEXT)])
        style.configure("Hist.TFrame", background=PANEL_2)

        nb = ttk.Notebook(win, style="Hist.TNotebook")
        nb.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 6))

        self._hist_texts: dict[str, tk.Text] = {}
        for tab_name, _tag in [("commands", "h"), ("tools", "h"), ("metrics", "h")]:
            frame = ttk.Frame(nb, style="Hist.TFrame")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            text = tk.Text(frame, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, wrap="word", font=("Consolas", 9), padx=10, pady=8)
            text.grid(row=0, column=0, sticky="nsew")
            sb = tk.Scrollbar(frame, command=text.yview, bg=PANEL, troughcolor=TERM, activebackground=STROKE)
            sb.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=sb.set)
            text.configure(state=tk.DISABLED)
            for tag_name, color in [("h", CYAN), ("d", TEXT), ("m", MUTED), ("ok", GREEN), ("warn", AMBER), ("err", RED)]:
                text.tag_configure(tag_name, foreground=color)
            nb.add(frame, text=tab_name.upper())
            self._hist_texts[tab_name] = text

        status = tk.Label(win, text="Loading history\u2026", bg=BG, fg=MUTED, font=("JetBrains Mono", 8))
        status.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 12))
        self._hist_status = status

        self._hist_win = win
        self._history_load()

    def _history_load(self) -> None:
        def worker() -> None:
            try:
                data = {
                    "commands": self.runtime.orchestrator.get_history(limit=60),
                    "tools": self.runtime.orchestrator.get_tool_history(limit=80),
                    "metrics": self.runtime.orchestrator.get_metrics(limit=500),
                }
                self._events.put(("history_data", data))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _fill_history_tab(self, key: str, lines: list[tuple[str, str]]) -> None:
        text = self._hist_texts.get(key)
        if text is None or not text.winfo_exists():
            return
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        for tag, chunk in lines:
            text.insert(tk.END, chunk, (tag,))
        text.configure(state=tk.DISABLED)

    def _handle_history_data(self, data: dict[str, Any]) -> None:
        if not getattr(self, "_hist_win", None) or not self._hist_win.winfo_exists():
            return

        cmd_lines: list[tuple[str, str]] = []
        for row in (data.get("commands") or [])[:60]:
            cmd_lines.append(("d", f"  [{row.get('created_at', '')[:19]}] {row.get('source', '?')}: {row.get('command', '')}\n"))
            snippet = str(row.get("response_text") or "")[:160].replace("\n", " ")
            if snippet:
                cmd_lines.append(("m", f"      {snippet}\n"))
        self._fill_history_tab("commands", cmd_lines or [("m", "No command history yet.")])

        tool_lines: list[tuple[str, str]] = []
        for row in (data.get("tools") or [])[:80]:
            status = str(row.get("status", "?"))
            risk = str(row.get("risk", "low"))
            tool_lines.append(("d", f"  [{row.get('created_at', '')[:19]}] {row.get('tool', '?')} [{status}]\n"))
            tool_lines.append(("m", f"      {row.get('command', '')}  ·  risk: {risk}{'  ·  ' + str(row.get('error') or '') if row.get('error') else ''}\n"))
        self._fill_history_tab("tools", tool_lines or [("m", "No tool activity yet.")])

        metrics = data.get("metrics") or {}
        summary = metrics.get("summary") or {}
        m_lines: list[tuple[str, str]] = [
            ("h", "METRICS SUMMARY\n"),
            ("d", f"  window: {summary.get('window', 0)} executions\n"),
            ("d", f"  total commands: {summary.get('total_commands', 0)}\n"),
            ("d", f"  success rate: {float(summary.get('success_rate', 0)) * 100:.1f}%\n"),
            ("d", f"  failure rate: {float(summary.get('failure_rate', 0)) * 100:.1f}%\n"),
            ("d", f"  avg duration: {int(summary.get('avg_duration_ms', 0))} ms\n"),
        ]
        status_counts = summary.get("status_counts") or {}
        if status_counts:
            m_lines.append(("h", "\nBY STATUS\n"))
            for status, count in status_counts.items():
                m_lines.append(("d", f"  {status}: {count}\n"))
        top_failed = summary.get("top_failed_tools") or []
        if top_failed:
            m_lines.append(("h", "\nTOP FAILED TOOLS\n"))
            for item in top_failed:
                name = item.get("tool") if isinstance(item, dict) else item
                cnt = item.get("failures") if isinstance(item, dict) else ""
                m_lines.append(("d", f"  {name}: {cnt}\n"))
        recent = (metrics.get("recent") or [])[:30]
        if recent:
            m_lines.append(("h", "\nRECENT EXECUTIONS\n"))
            for row in recent:
                m_lines.append(("d", f"  [{row.get('created_at', '')[:19]}] {row.get('command', '')} \u2192 {row.get('status', '?')} ({int(row.get('duration_ms', 0))}ms)\n"))
        self._fill_history_tab("metrics", m_lines)
        self._hist_status.configure(text=f"Loaded at {datetime.now().strftime('%H:%M:%S')}")

    # ---- tasks window ---------------------------------------------------------

    def _open_tasks(self) -> None:
        tasks_win = getattr(self, "_tasks_win", None)
        if tasks_win is not None and tasks_win.winfo_exists():
            tasks_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 Tasks & Progress")
        win.configure(bg=BG)
        win.geometry("840x580")
        win.transient(self.root)
        win.rowconfigure(1, weight=1)
        win.columnconfigure(0, weight=1)

        head = tk.Frame(win, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="TASKS & PROGRESS", bg=BG, fg=CYAN, font=("JetBrains Mono", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Button(
            head, text="REFRESH", command=self._tasks_load, bg=BTN2, fg=TEXT,
            relief=tk.FLAT, bd=0, padx=8, pady=3, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=1, sticky="e")

        list_panel = self._make_panel(win, PANEL_2)
        list_panel.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        list_panel.rowconfigure(1, weight=1)
        list_panel.columnconfigure(0, weight=1)
        tk.Label(list_panel, text="TRACKED TASKS", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2), columnspan=2
        )
        self._tasks_listbox = tk.Listbox(list_panel, bg=LIST, fg=TEXT, relief=tk.FLAT, bd=0, font=("Consolas", 9), selectbackground=SEL, height=8)
        self._tasks_listbox.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 6))
        tasks_sb = tk.Scrollbar(list_panel, command=self._tasks_listbox.yview, bg=PANEL, troughcolor=LIST, activebackground=STROKE)
        tasks_sb.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 6))
        self._tasks_listbox.configure(yscrollcommand=tasks_sb.set)
        self._tasks_listbox.bind("<<ListboxSelect>>", self._show_task_detail)

        detail = self._make_panel(win, PANEL_2)
        detail.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 8))
        detail.rowconfigure(1, weight=1)
        detail.columnconfigure(0, weight=1)
        tk.Label(detail, text="TASK DETAIL", bg=PANEL_2, fg=MUTED, font=("JetBrains Mono", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2), columnspan=2
        )
        self._task_detail_text = tk.Text(detail, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, wrap="word", font=("Consolas", 9), padx=8, pady=6, height=10)
        self._task_detail_text.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        detail_sb = tk.Scrollbar(detail, command=self._task_detail_text.yview, bg=PANEL, troughcolor=TERM, activebackground=STROKE)
        detail_sb.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        self._task_detail_text.configure(yscrollcommand=detail_sb.set)
        self._task_detail_text.configure(state=tk.DISABLED)
        for tag_name, color in [("h", CYAN), ("d", TEXT), ("m", MUTED), ("ok", GREEN), ("warn", AMBER), ("err", RED)]:
            self._task_detail_text.tag_configure(tag_name, foreground=color)

        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=("JetBrains Mono", 8))
        status.grid(row=3, column=0, sticky="w", padx=14, pady=(0, 12))
        self._tasks_status = status

        self._tasks_win = win
        self._tasks_items: list[dict[str, Any]] = []
        self._tasks_load()
        self._tasks_poll()

    def _tasks_poll(self) -> None:
        alive = self.root.winfo_exists() and getattr(self, "_tasks_win", None) is not None and self._tasks_win.winfo_exists()
        if not alive:
            return
        self._tasks_load()
        self.root.after(4000, self._tasks_poll)

    def _tasks_load(self) -> None:
        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("task_list", {"limit": 50})
                self._events.put(("tasks_list", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_tasks_list(self, payload: dict[str, Any]) -> None:
        if not getattr(self, "_tasks_win", None) or not self._tasks_win.winfo_exists():
            return
        items = payload.get("items") or []
        self._tasks_items = items
        box = self._tasks_listbox
        box.delete(0, tk.END)
        if not items:
            box.insert(tk.END, "No tasks yet \u2014 ask JARVIS to 'plan' something, or create one via the planner.")
        for item in items:
            progress = item.get("progress") or {}
            status = str(item.get("status", "?")).upper()
            box.insert(
                tk.END,
                f"[{status}] {item.get('id', '')}  {str(item.get('command', ''))[:48]}  |  {progress.get('completed', 0)}/{progress.get('total', 0)} steps ({progress.get('percent', 0)}%)",
            )
        self._tasks_status.configure(text=f"{len(items)} task(s) \u00b7 auto-refreshes every 4s")

    def _show_task_detail(self, _event: tk.Event | None = None) -> None:
        sel = self._tasks_listbox.curselection()
        if not sel or sel[0] >= len(self._tasks_items):
            return
        task_id = str(self._tasks_items[sel[0]].get("id", ""))

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("task_status", {"task_id": task_id})
                self._events.put(("task_detail", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_task_detail(self, payload: dict[str, Any]) -> None:
        text = getattr(self, "_task_detail_text", None)
        if text is None or not text.winfo_exists():
            return
        lines: list[tuple[str, str]] = [
            ("h", f"TASK {payload.get('task_id', '?')}\n"),
            ("d", f"  command: {payload.get('command', '')}\n"),
            ("d", f"  status: {payload.get('status', '?')}\n"),
        ]
        progress = payload.get("progress") or {}
        lines.append(("d", f"  progress: {progress.get('completed', 0)}/{progress.get('total', 0)} steps ({progress.get('percent', 0)}%), current: {progress.get('current_step') or 'none'}\n"))
        if payload.get("error"):
            lines.append(("err", f"  error: {payload.get('error')}\n"))
        lines.append(("h", "\nSTEPS\n"))
        for step in payload.get("steps") or []:
            line = f"  [{step.get('status', '?')}] {step.get('tool', '?')}"
            if step.get("error"):
                line += f" \u2014 {step.get('error')}"
            if step.get("attempts", 1) > 1:
                line += f" (attempts: {step.get('attempts')})"
            lines.append(("d", line + "\n"))
        if not (payload.get("steps") or []):
            lines.append(("m", "  no steps recorded\n"))
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        for tag, chunk in lines:
            text.insert(tk.END, chunk, (tag,))
        text.configure(state=tk.DISABLED)

    # ---- memory & knowledge window --------------------------------------------

    def _open_memory(self) -> None:
        mem_win = getattr(self, "_mem_win", None)
        if mem_win is not None and mem_win.winfo_exists():
            mem_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("JARVIS \u2014 Memory & Knowledge")
        win.configure(bg=BG)
        win.geometry("840x580")
        win.transient(self.root)
        win.rowconfigure(2, weight=1)
        win.columnconfigure(0, weight=1)

        head = tk.Frame(win, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="MEMORY & KNOWLEDGE", bg=BG, fg=CYAN, font=("JetBrains Mono", 12, "bold")).grid(row=0, column=0, sticky="w")
        search_wrap = tk.Frame(head, bg=SHELL, padx=1, pady=1)
        search_wrap.grid(row=0, column=1, sticky="ew", padx=10)
        self._mem_query = tk.Entry(search_wrap, bg=CANVAS, fg=TEXT, insertbackground=CYAN, relief=tk.FLAT, bd=0, font=("JetBrains Mono", 9))
        self._mem_query.pack(fill="x", ipady=4)
        self._mem_query.bind("<Return>", lambda _: self._memory_recall())
        tk.Button(
            head, text="SEARCH", command=self._memory_recall, bg=INDIGO, fg="#05101b", relief=tk.FLAT, bd=0,
            padx=8, pady=3, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=2, sticky="e")
        tk.Button(
            head, text="REFRESH", command=self._memory_load, bg=BTN2, fg=TEXT,
            relief=tk.FLAT, bd=0, padx=8, pady=3, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Mem.TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("Mem.TNotebook.Tab", background=PANEL_2, foreground=TEXT, padding=(14, 7), font=("JetBrains Mono", 9, "bold"))
        style.map("Mem.TNotebook.Tab", background=[("selected", SEL)], foreground=[("selected", TEXT)])
        style.configure("Mem.TFrame", background=PANEL_2)

        nb = ttk.Notebook(win, style="Mem.TNotebook")
        nb.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 6))

        self._mem_texts: dict[str, tk.Text] = {}
        for tab_name in ("episodes", "knowledge", "search"):
            frame = ttk.Frame(nb, style="Mem.TFrame")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            text = tk.Text(frame, bg=TERM, fg=TEXT, relief=tk.FLAT, bd=0, wrap="word", font=("Consolas", 9), padx=10, pady=8)
            text.grid(row=0, column=0, sticky="nsew")
            sb = tk.Scrollbar(frame, command=text.yview, bg=PANEL, troughcolor=TERM, activebackground=STROKE)
            sb.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=sb.set)
            text.configure(state=tk.DISABLED)
            for tag_name, color in [("h", CYAN), ("d", TEXT), ("m", MUTED), ("ok", GREEN), ("warn", AMBER), ("err", RED)]:
                text.tag_configure(tag_name, foreground=color)
            nb.add(frame, text=tab_name.upper())
            self._mem_texts[tab_name] = text

        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=("JetBrains Mono", 8))
        status.grid(row=3, column=0, sticky="w", padx=14, pady=(0, 12))
        self._mem_status = status

        self._mem_win = win
        self._memory_load()

    def _memory_load(self) -> None:
        def worker() -> None:
            try:
                episodes = self.runtime.registry.execute("memory_list", {"limit": 25})
                knowledge = self.runtime.registry.execute("knowledge_stats", {})
                self._events.put(("memory_data", {"episodes": episodes, "knowledge": knowledge}))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_memory_data(self, data: dict[str, Any]) -> None:
        if not getattr(self, "_mem_win", None) or not self._mem_win.winfo_exists():
            return
        episodes = data.get("episodes") or {}
        ep_items = episodes.get("episodes") or []
        ep_lines: list[tuple[str, str]] = [("h", f"EPISODIC MEMORY ({episodes.get('count', len(ep_items))})\n")]
        if not ep_items:
            ep_lines.append(("m", "  no episodic memories yet\n"))
        for ep in ep_items[:25]:
            ep_lines.append(("d", f"  [{ep.get('id')}] {str(ep.get('command', ''))[:70]} \u2014 {ep.get('status', '?')}\n"))
            if ep.get("summary"):
                ep_lines.append(("m", f"      {str(ep.get('summary', ''))[:140]}\n"))
        self._fill_memory_tab("episodes", ep_lines)

        knowledge = data.get("knowledge") or {}
        stats = knowledge.get("stats") or {}
        kn_lines: list[tuple[str, str]] = [("h", "KNOWLEDGE BASE\n")]
        if stats:
            for key, value in stats.items():
                kn_lines.append(("d", f"  {key}: {value}\n"))
        else:
            kn_lines.append(("m", "  knowledge base empty \u2014 use 'remember' or 'index' commands\n"))
        self._fill_memory_tab("knowledge", kn_lines)

    def _memory_recall(self) -> None:
        query = (self._mem_query.get() or "").strip()
        if not query:
            return

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("recall", {"query": query, "top_k": 8})
                self._events.put(("memory_search", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_memory_search(self, payload: dict[str, Any]) -> None:
        if not getattr(self, "_mem_win", None) or not self._mem_win.winfo_exists():
            return
        results = payload.get("results") or []
        lines: list[tuple[str, str]] = [("h", f"SEARCH: {payload.get('query', '')}\n")]
        if not results:
            lines.append(("m", "  no matches\n"))
        for hit in results[:20]:
            title = str(hit.get("title") or hit.get("doc_type") or "?")
            score = hit.get("score")
            content = str(hit.get("content") or "")[:160]
            score_str = f"  ({score:.2f})" if isinstance(score, (int, float)) else ""
            lines.append(("d", f"  {title}{score_str}\n"))
            if content:
                lines.append(("m", f"      {content}\n"))
        self._fill_memory_tab("search", lines)

    def _fill_memory_tab(self, key: str, lines: list[tuple[str, str]]) -> None:
        text = self._mem_texts.get(key)
        if text is None or not text.winfo_exists():
            return
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        for tag, chunk in lines:
            text.insert(tk.END, chunk, (tag,))
        text.configure(state=tk.DISABLED)

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
            self.voice_switch_button.configure(text="VOICE ON", bg=OK, fg=GREEN)
            self.online_label.configure(text="STATUS: STREAMING")
        else:
            self.voice_switch_button.configure(text="VOICE OFF", bg=BTN2, fg=TEXT)
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
        self.root.after(50, self._animate_wave)

    def _refresh_approvals(self) -> None:
        def worker() -> None:
            try:
                items = [i.model_dump() for i in self.runtime.orchestrator.list_pending_approvals()]
                self._events.put(("approvals", items))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _diagnose_system(self) -> None:
        """Run diagnose_system tool off the UI thread and report to the terminal."""
        self._append_terminal("SYS", "Running system diagnosis\u2026")

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("diagnose_system", {})
                self._events.put(("diagnosis", payload))
            except Exception as exc:
                self._events.put(("diagnosis_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_diagnosis(self, payload: dict[str, Any]) -> None:
        status = payload.get("status", "info")
        if status == "completed":
            diagnosis = payload.get("diagnosis", {})
            explanation = payload.get("explanation", "")
            raw_metrics = payload.get("raw_metrics", {})
            if diagnosis.get("root_cause"):
                self._append_terminal("SYS", f"Diagnosis: issue detected \u2014 {diagnosis['root_cause']}")
            else:
                self._append_terminal("SYS", f"Diagnosis: no issues detected \u2014 {explanation}")
            if raw_metrics:
                cpu = raw_metrics.get("cpu_percent", "N/A")
                mem = raw_metrics.get("memory_percent", "N/A")
                self._append_terminal("SYS", f"  CPU: {cpu}% \u2022 RAM: {mem}%")
        else:
            self._append_terminal("WARN", f"Diagnosis not available: {payload.get('explanation', 'unknown')}")

    def _handle_diagnosis_error(self, message: str) -> None:
        self._append_terminal("ERR", f"Diagnosis failed: {message}")

    def _render_approval_cards(self, items: list[dict[str, Any]]) -> None:
        inner = getattr(self, "approval_inner", None)
        if inner is None:
            return
        for widget in inner.winfo_children():
            widget.destroy()
        self._approval_cards.clear()
        self._pending_ids = [str(item.get("id") or item.get("approval_id") or "") for item in items]
        for item in items:
            card = tk.Frame(inner, bg=INNER, highlightbackground=STROKE, highlightthickness=1, bd=0)
            card.pack(fill="x", padx=6, pady=3)
            prompt = str(item.get("prompt") or item.get("command") or "")
            tk.Label(
                card, text=prompt[:90] or "(no prompt)", bg=INNER, fg=TEXT, font=("JetBrains Mono", 8, "bold"),
                anchor="w", justify="left", wraplength=240,
            ).pack(fill="x", padx=6, pady=(4, 0))
            detail = str(item.get("reason") or item.get("summary") or item.get("description") or "")
            if detail:
                tk.Label(
                    card, text=detail[:140], bg=INNER, fg=MUTED, font=("JetBrains Mono", 8),
                    anchor="w", justify="left", wraplength=240,
                ).pack(fill="x", padx=6)
            actions = tk.Frame(card, bg=INNER)
            actions.pack(fill="x", padx=6, pady=4)
            approval_id = str(item.get("id") or item.get("approval_id") or "")

            def _make_approve(aid: str) -> Callable[[], None]:
                return lambda: self._approve_id(aid)

            def _make_reject(aid: str) -> Callable[[], None]:
                return lambda: self._reject_id(aid)

            tk.Button(
                actions, text="APPROVE", command=_make_approve(approval_id), bg=OK, fg=GREEN,
                relief=tk.FLAT, bd=0, padx=8, pady=2, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
            ).pack(side="left", padx=(0, 6))
            tk.Button(
                actions, text="REJECT", command=_make_reject(approval_id), bg=DANGER, fg=RED,
                relief=tk.FLAT, bd=0, padx=8, pady=2, font=("JetBrains Mono", 8, "bold"), cursor="hand2",
            ).pack(side="left")
            self._approval_cards[approval_id] = card
        if self._approval_badge is not None:
            count = len(items)
            self._approval_badge.configure(
                text=str(count) if count else "0", bg=AMBER if count else BTN2, fg="#021018" if count else TEXT,
            )
        try:
            self.approval_canvas.configure(scrollregion=self.approval_canvas.bbox("all"))
        except Exception:
            pass
        if len(items) > self._last_pending_count and items:
            self.root.bell()
            self._toaster.notify(
                "Approval pending", f"{len(items)} action(s) waiting for you.", AMBER, on_click=self._raise_main_window
            )
        self._last_pending_count = len(items)

    def _approve_id(self, approval_id: str) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                res = self.runtime.orchestrator.approve(approval_id)
                self._events.put(("approval_result", res.model_dump()))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _reject_id(self, approval_id: str) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                res = self.runtime.orchestrator.reject(approval_id)
                self._events.put(("approval_result", res.model_dump()))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_hub_event(self, topic: str, payload: dict[str, Any]) -> None:
        text = ""
        tag = "feed_sys"
        if topic == "command_start":
            text = f"\u25b8 {payload.get('command', '')}"
            tag = "feed_cmd"
        elif topic == "command_done":
            status = payload.get("status", "done")
            text = f"\u2713 {payload.get('command', '')} \u2192 {status}"
            tag = "feed_ok" if str(status) not in {"error", "failed"} else "feed_err"
        elif topic == "agent_step":
            text = f"\u2699 {payload.get('step', '')}"
            tag = "feed_pending"
        elif topic == "approval":
            self._refresh_approvals()
        elif topic == "scheduler_run":
            text = f"\u23f1 scheduled: {payload.get('command', '')} [{payload.get('status', '')}]"
        if text:
            self._append_feed(tag, text)

    def _bind_hotkeys(self) -> None:
        for i in range(1, 10):
            def _open_alt(_e: tk.Event, idx: int = i - 1) -> None:
                self._open_app_by_index(idx)

            self.root.bind_all(f"<Alt-Key-{i}>", _open_alt)
        self.root.bind_all("<F1>", lambda _: self._open_help())
        self.root.bind_all("<Control-l>", lambda _: self._clear_terminal())
        self.root.bind_all("<Control-k>", lambda _: self._clear_feed())

    def _scheduler_on_run(self, job: dict[str, Any], status: str, text: str) -> None:
        self._events.put(("sched_run", (job, status, text)))
        try:
            self._hub.publish(
                "scheduler_run",
                {"command": job.get("command"), "status": status, "result": text, "job_id": job.get("id")},
            )
        except Exception:
            pass

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
            self.chat_text.insert(tk.END, "YOU\n", ("chat_user_label",))
            self.chat_text.insert(tk.END, text + "\n\n", ("chat_user",))
        else:
            self.chat_text.insert(tk.END, "JARVIS\n", ("chat_ai_label",))
            self.chat_text.insert(tk.END, text + "\n\n", ("chat_ai",))
        self.chat_text.see(tk.END)
        self.chat_text.configure(state=tk.DISABLED)

    def _start_chat_stream(self) -> None:
        self.chat_text.configure(state=tk.NORMAL)
        self.chat_text.insert(tk.END, "JARVIS\n", ("chat_ai_label",))
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
        msgs: list[Any] = [{"role": "system", "content": "You are AI Bot inside Jarvis HUD. Keep answers concise and useful."}]
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
        self._draw_background()
        cx, cy = int(w * 0.68), int(h * 0.72)
        self.scene.create_oval(cx - 90, cy - 90, cx + 90, cy + 90, outline=INDIGO, width=1, tags="ring")
        self.scene.create_oval(cx - 120, cy - 120, cx + 120, cy + 120, outline=RING2, width=1, tags="ring2")
        self.scene.create_rectangle(cx - 86, cy - 8, cx + 86, cy + 2, fill=CYAN, outline="")
        self.scene.create_rectangle(cx - 58, cy - 8, cx - 48, cy + 72, fill=INDIGO, outline="")
        self.scene.create_rectangle(cx + 48, cy - 8, cx + 58, cy + 72, fill=INDIGO, outline="")
        self._scene_particles.clear()
        count = 40 if self._gfx_full else 12
        for _i in range(count):
            x = random.uniform(0, w)
            y = random.uniform(0, h)
            r = random.uniform(2, 5)
            speed = random.uniform(0.2, 0.9)
            node = self.scene.create_oval(x - r, y - r, x + r, y + r, fill=INDIGO, outline="")
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
        self._append_terminal(OK, payload.get("response_text", ""))
        has_failure = False
        has_pending = False
        for item in payload.get("results") or []:
            tool = item.get("tool")
            status = item.get("status")
            if status == "completed":
                self._append_terminal(OK, f"{tool}: completed")
            elif status == "pending":
                self._append_terminal("WARN", f"{tool}: pending approval")
                has_pending = True
            else:
                self._append_terminal("ERR", f"{tool}: {item.get('error')}")
                has_failure = True
        command = str(payload.get("command", "command"))
        if has_failure:
            self._append_feed("feed_err", f"\u2717 {command} FAILED")
            self.root.bell()
            self._toaster.notify("Command failed", command, RED)
        elif has_pending:
            self._append_feed("feed_pending", f"\u23f3 {command} awaiting approval")
            self.root.bell()
            self._toaster.notify("Approval required", command, AMBER, on_click=self._raise_main_window)
        else:
            self._append_feed("feed_ok", f"\u2713 {command} done")
        self._refresh_approvals()

    def _handle_agent_step(self, step: dict[str, Any]) -> None:
        tool = str(step.get("tool", "?"))
        status = str(step.get("status", "?"))
        iteration = int(step.get("iteration", 1))
        snippet = str(step.get("output_snippet", "") or "").strip()
        line = f"[agent i{iteration}] \u25b8 {tool} [{status}]"
        if snippet:
            line += f" \u2014 {snippet}"
        if status == "failed":
            self._append_terminal("ERR", line)
            self._append_feed("feed_err", f"\u2717 agent: {tool} failed")
        elif status == "pending":
            self._append_terminal("WARN", line)
            self._append_feed("feed_pending", f"\u23f3 agent: {tool} pending approval")
        else:
            self._append_terminal(OK, line)
            self._append_feed("feed_cmd", f"\u25b8 agent: {tool} done")

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if event == "command_result":
                self._handle_command_result(payload)
            elif event == "agent_step":
                self._handle_agent_step(payload)
            elif event == "voice":
                self._set_busy(False)
                self._wave_active = bool(payload.get("running"))
                self._refresh_voice_button()
            elif event == "approvals":
                self._render_approval_cards(payload)
            elif event == "hub_event":
                topic, payload2 = payload
                self._handle_hub_event(topic, payload2)
            elif event == "summon_mini":
                self._raise_main_window()
                self.mini.show()
                self._toaster.notify("JARVIS", "Mini HUD summoned \u2014 Ctrl+Shift+J", CYAN, duration_ms=2500)
            elif event == "approval_result":
                self._set_busy(False)
                self._append_terminal(OK, payload.get("message", ""))
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
                self._append_terminal(OK, str(payload))
                self._append_feed("feed_sys", str(payload))
            elif event == "sched_run":
                job, status, text = payload
                tag = OK if str(status) != "failed" else "ERR"
                self._append_terminal(tag, f"[scheduler] {job.get('command')} \u2192 {status}: {text}")
                self._append_feed("feed_sys", f"\u23f1 scheduled: {job.get('command')} [{status}]")
            elif event == "sched_list":
                self._handle_sched_list(payload)
            elif event == "sched_added":
                self._append_terminal(OK, f"[scheduler] added: {payload}")
                self._append_feed("feed_sys", f"\u23f1 scheduled: {payload}")
                if getattr(self, "_sched_status", None) is not None:
                    self._sched_status.configure(text=f"Scheduled: {payload}", fg=GREEN)
                self._scheduler_load()
            elif event == "sched_error":
                self._append_terminal("ERR", f"[scheduler] {payload}")
                if getattr(self, "_sched_status", None) is not None:
                    self._sched_status.configure(text=str(payload), fg=RED)
            elif event == "sched_changed":
                self._scheduler_load()
            elif event == "history_data":
                self._handle_history_data(payload)
            elif event == "tasks_list":
                self._handle_tasks_list(payload)
            elif event == "task_detail":
                self._handle_task_detail(payload)
            elif event == "memory_data":
                self._handle_memory_data(payload)
            elif event == "memory_search":
                self._handle_memory_search(payload)
            elif event == "diagnosis":
                self._handle_diagnosis(payload)
            elif event == "diagnosis_error":
                self._handle_diagnosis_error(str(payload))
            elif event == "error":
                self._set_busy(False)
                self._append_terminal("ERR", str(payload))
                self.root.bell()
                self._toaster.notify("JARVIS error", str(payload), RED)
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
        totals = payload.get("totals") or (task.get("totals") if isinstance(task, dict) else None) or {}
        for name in ("INTERVIEW", "OFFER", "REJECTED"):
            self.job_stat_labels[name].configure(text=f"{name} {int(totals.get(name.lower(), 0))}")
        try:
            max_apps = int(task.get("max_applications") or 0)
            pct = round((applied / max_apps) * 100) if max_apps else 0
            self.job_progress.configure(value=max(0, min(100, pct)))
        except Exception:
            self.job_progress.configure(value=0)
        recent = payload.get("recent") or []
        recent_box = getattr(self, "recent_list", None)
        if recent_box is not None:
            recent_box.delete(0, tk.END)
            for item in recent[:12]:
                title = str(item.get("title") or "?")
                company = str(item.get("company") or item.get("site") or "?")
                ts = str(item.get("created_at") or "")[:16].replace("T", " ")
                recent_box.insert(tk.END, f"{ts}  {title} @ {company}")
        if status == "paused" and self._job_prev_status != "paused":
            self.root.bell()
            self._toaster.notify("Job paused", reason or "Task paused, waiting on you.", AMBER, on_click=self._raise_main_window)
        self._job_prev_status = status

    def _on_close(self) -> None:
        try:
            self.runtime.voice.stop()
        except Exception:
            pass
        try:
            self.runtime.scheduler.stop()
        except Exception:
            pass
        try:
            self._monitor.stop()
        except Exception:
            pass
        try:
            if self._hotkey is not None:
                self._hotkey.stop()
        except Exception:
            pass
        try:
            self._tray.stop()
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
    from app.core.single_instance import SingleInstance

    if not SingleInstance("hud").acquire():
        return  # another HUD instance is already running; exit silently
    JarvisHudApp().run()


if __name__ == "__main__":
    main()
