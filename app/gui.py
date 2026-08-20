from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import random
import shutil
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QKeySequence, QShortcut

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


def _rgba(hex_color: str, alpha: int) -> str:
    c = hex_color.lstrip("#")
    if len(c) == 6:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"
    return f"rgba(0, 0, 0, {alpha})"


def _blend(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    return f"#{int(r1 + (r2 - r1) * t):02x}{int(g1 + (g2 - g1) * t):02x}{int(b1 + (b2 - b1) * t):02x}"


def _build_qss() -> str:
    return f"""
    * {{ font-family: 'Segoe UI'; }}
    QMainWindow, QWidget {{ background: transparent; }}
    QLabel {{ color: {TEXT}; font-size: 12px; }}
    QFrame#Header {{
        background: {_rgba(BG, 150)};
        border: 1px solid {_rgba(STROKE, 180)};
        border-radius: 14px;
    }}
    QFrame#Card {{
        background: {_rgba(PANEL_2, 195)};
        border: 1px solid {_rgba(STROKE, 180)};
        border-radius: 14px;
    }}
    QFrame#CardTitleBar {{ background: transparent; }}
    QLabel#CardTitle {{ color: {TEXT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#CardSubtitle {{ color: {MUTED}; font-size: 10px; }}
    QPushButton {{
        color: {TEXT};
        background: {_rgba(CHIP, 200)};
        border: 1px solid {_rgba(STROKE, 120)};
        border-radius: 10px;
        padding: 6px 12px;
        font-size: 11px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {_rgba(CHIP_HI, 230)}; }}
    QPushButton:pressed {{ background: {_rgba(SEL, 230)}; }}
    QPushButton#Primary {{
        color: {BG};
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {CYAN}, stop:1 {INDIGO});
        border: none;
        font-weight: 800;
        padding: 8px 16px;
    }}
    QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_blend(CYAN, '#ffffff', 0.15)}, stop:1 {_blend(INDIGO, '#ffffff', 0.15)}); }}
    QPushButton#Danger {{ background: {_rgba(DANGER, 200)}; color: {RED}; }}
    QPushButton#Success {{ background: {_rgba(OK, 200)}; color: {GREEN}; }}
    QLineEdit, QComboBox {{
        background: {_rgba(BTN2, 235)};
        border: 1px solid {_rgba(CYAN, 90)};
        border-radius: 10px;
        padding: 8px 10px;
        color: {TEXT};
        font-size: 12px;
        selection-background-color: {SEL};
    }}
    QLineEdit:hover {{ border: 1px solid {_rgba(CYAN, 160)}; }}
    QLineEdit:focus {{
        border: 1px solid {CYAN};
        background: {_rgba(SEL, 200)};
    }}
    QLineEdit::placeholder {{ color: {MUTED}; }}
    QTextEdit, QPlainTextEdit {{
        background: {_rgba(TERM, 220)};
        border: 1px solid {_rgba(STROKE, 160)};
        border-radius: 10px;
        color: {TEXT};
        padding: 6px;
        font-family: 'Consolas', 'Cascadia Mono', monospace;
        font-size: 12px;
        selection-background-color: {SEL};
    }}
    QListWidget {{
        background: {_rgba(LIST, 220)};
        border: 1px solid {_rgba(STROKE, 160)};
        border-radius: 10px;
        color: {TEXT};
        font-size: 12px;
        padding: 4px;
    }}
    QListWidget::item {{ padding: 5px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {SEL}; }}
    QProgressBar {{
        background: {_rgba(TERM, 200)};
        border: 1px solid {_rgba(STROKE, 140)};
        border-radius: 6px;
        height: 10px;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {CYAN}; border-radius: 5px; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {_rgba(CHIP, 200)}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {_rgba(CHIP_HI, 220)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {_rgba(CHIP, 200)}; border-radius: 5px; min-width: 30px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QMenu {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {STROKE}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {SEL}; }}
    QTabWidget::pane {{ border: 1px solid {_rgba(STROKE, 160)}; border-radius: 10px; background: {_rgba(TERM, 180)}; }}
    QTabBar::tab {{ background: {_rgba(CHIP, 180)}; color: {MUTED}; padding: 7px 16px; border-radius: 8px 8px 0 0; margin-right: 3px; font-weight: 600; }}
    QTabBar::tab:selected {{ background: {SEL}; color: {TEXT}; }}
    QSplitter::handle {{ background: {_rgba(STROKE, 120)}; border-radius: 2px; }}
    QToolTip {{
        background: {TOOLTIP}; color: {TEXT};
        border: 1px solid {TOOLTIP_BORDER};
        padding: 5px; border-radius: 6px;
    }}
    """


class Backdrop(QWidget):
    """Paints the animated background: aurora / gradient / image with drifting particles."""

    GRADIENTS: list[tuple[str, str, str]] = [
        ("aurora", "#0b152b", "#0a1e3d"),
        ("sunset", "#2b1b3d", "#0f2a3d"),
        ("mono", "#0a0a12", "#151526"),
        ("ocean", "#061a2e", "#0e3a4a"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._mode = "aurora"
        self._value = ""
        self._image: QPixmap | None = None
        self._angle = 0.0
        self._particles: list[dict[str, Any]] = []
        self._gfx_full = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(40)

    def set_bg(self, mode: str, value: str = "") -> None:
        self._mode = mode
        self._value = value
        self._image = None
        if mode == "image":
            path = background_image_path(_backgrounds_dir(), value) if value else None
            if path is not None:
                try:
                    self._image = QPixmap(str(path))
                except Exception:
                    self._image = None
        self._particles.clear()
        self.update()

    def set_gfx(self, full: bool) -> None:
        self._gfx_full = full
        self._particles.clear()

    def _animate(self) -> None:
        self._angle += 0.03
        if not self._particles and self.width() > 10:
            w, h = self.width(), self.height()
            count = 50 if self._gfx_full else 14
            for _ in range(count):
                self._particles.append(
                    {
                        "x": random.uniform(0, w),
                        "y": random.uniform(0, h),
                        "r": random.uniform(1.5, 4.0),
                        "speed": random.uniform(0.2, 0.9),
                    }
                )
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(1, self.width())
        h = max(1, self.height())

        if self._mode == "image" and self._image is not None:
            pix = self._image
            scaled = pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - w) // 2
            y = (scaled.height() - h) // 2
            p.drawPixmap(0, 0, scaled, x, y, w, h)
            p.fillRect(0, 0, w, h, QColor(4, 8, 20, 170))
        elif self._mode == "gradient":
            idx = int(self._value or 0) % len(self.GRADIENTS)
            _name, top, bottom = self.GRADIENTS[idx]
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor(top))
            grad.setColorAt(1, QColor(bottom))
            p.fillRect(0, 0, w, h, grad)
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor("#050a14"))
            grad.setColorAt(1, QColor("#0e1b33"))
            p.fillRect(0, 0, w, h, grad)
            # aurora blobs
            self._draw_blob(p, w * 0.22, h * 0.35, 320 + 30 * math.sin(self._angle), INDIGO, 26)
            self._draw_blob(p, w * 0.8, h * 0.7, 380 + 40 * math.cos(self._angle * 0.7), CYAN, 18)

        # particles
        for part in self._particles:
            part["y"] += part["speed"]
            part["x"] += math.sin(self._angle + part["y"] * 0.02) * 0.4
            if part["y"] > h + 8:
                part["y"] = -8
                part["x"] = random.uniform(0, w)
            r = part["r"]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(129, 140, 248, 150))
            p.drawEllipse(QPointF(part["x"], part["y"]), r, r)

        # arc-reactor orb
        cx, cy = int(w * 0.86), int(h * 0.84)
        r1 = 90 + (3 * math.sin(self._angle))
        r2 = 120 + (4 * math.cos(self._angle * 0.7))
        p.setPen(QPen(QColor(INDIGO), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r1, r1)
        p.drawEllipse(QPointF(cx, cy), r2, r2)
        for i in range(12):
            a = self._angle * 0.8 + i * math.pi / 6
            tick = QColor(CYAN)
            tick.setAlpha(160)
            p.setPen(QPen(tick, 2))
            x0 = cx + math.cos(a) * r1
            y0 = cy + math.sin(a) * r1
            x1 = cx + math.cos(a) * (r1 + 16)
            y1 = cy + math.sin(a) * (r1 + 16)
            p.drawLine(int(x0), int(y0), int(x1), int(y1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(34, 211, 238, 70))
        p.drawEllipse(QPointF(cx, cy), 40, 40)
        p.setBrush(QColor(34, 211, 238, 160))
        p.drawEllipse(QPointF(cx, cy), 18, 18)
        p.end()

    def _draw_blob(self, p: QPainter, x: float, y: float, radius: float, color: str, alpha: int) -> None:
        grad = QLinearGradient(x - radius, y, x + radius, y)
        c = QColor(color)
        c.setAlpha(alpha)
        grad.setColorAt(0, c)
        fade = QColor(color)
        fade.setAlpha(0)
        grad.setColorAt(1, fade)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(x, y), radius, radius)


class WaveWidget(QWidget):
    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__()
        self._on_click = on_click
        self._phase = 0.0
        self._label = "IDLE"
        self._color = MUTED
        self._amp = 6.0
        self._speed = 0.18
        self.setMinimumHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(50)

    def _step(self) -> None:
        self._phase += self._speed
        self.update()

    def update_status(self, label: str, color: str, amp: float, speed: float) -> None:
        self._label = label
        self._color = color
        self._amp = amp
        self._speed = speed

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(1, self.width())
        h = max(1, self.height())
        c = h // 2
        pen = QPen(QColor(self._color), 2)
        p.setPen(pen)
        path = QPainterPath()
        first = True
        for x in range(0, w + 1, 5):
            y = c + math.sin((x * 0.05) + self._phase) * self._amp
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        p.drawPath(path)
        p.setPen(QColor(self._color))
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        p.setFont(font)
        p.drawText(10, 16, f"VOICE {self._label}")
        p.end()


class JarvisHudApp(QMainWindow):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._hub = EventHub()
        self.runtime = build_runtime(events=self._hub)
        self._monitor = self.runtime.monitor
        self.setWindowTitle("JARVIS AI Command HUD")
        self.setMinimumSize(1280, 820)

        self._ui_config_path = Path(__file__).resolve().parent.parent / "data" / "jarvis_ui.json"
        self.mini = MiniHud(
            self, self.runtime, self._monitor, on_run_command=self._run_mini_command, on_open_main=self._raise_main_window
        )
        self.mini.hide()

        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy = False
        self._scene_angle = 0.0
        self._pending_ids: list[str] = []
        self._chat_streaming = False
        self._chat_history: list[dict[str, str]] = []
        self._cmd_history: list[str] = []
        self._cmd_history_index = -1
        self._last_pending_count = 0
        self._job_prev_status = ""
        self._theme = "aurora"
        self._gfx_full = False
        self._bg: dict[str, str] = {"mode": "aurora", "value": ""}
        self._approval_cards: dict[str, QWidget] = {}
        self._app_launch_items: list[dict[str, str]] = []
        self._all_app_launch_items: list[dict[str, str]] = []
        self._hub.subscribe(self._on_hub_event)

        self._build_ui()
        self._post_build()
        self._toaster = Toaster(self)
        self._tray = TrayIcon(on_show=self._tray_show, on_mini=self._tray_mini, on_exit=self._tray_exit)
        self._hotkey = GlobalHotkey()
        if self._hotkey.start(lambda: self._events.put(("summon_mini", None))):
            self._append_terminal("SYS", "Global hotkey Ctrl+Shift+J registered.")

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_events)
        self._poll_timer.start(80)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_voice_button)
        self._status_timer.start(500)

        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._refresh_status_meters)
        self._meter_timer.start(1000)

        self._job_timer = QTimer(self)
        self._job_timer.timeout.connect(self._job_tick)
        self._job_timer.start(5000)

        self._summon_timer = QTimer(self)
        self._summon_timer.timeout.connect(self._poll_summon_signal)
        self._summon_timer.start(1000)

        self._append_terminal("SYS", "HUD online.")

        self._restore_ui_config()
        self.runtime.scheduler.on_run = self._scheduler_on_run
        self.runtime.scheduler.start()
        self._maybe_show_recap()
        self._job_tick()

    # ------------------------------------------------------------ UI build

    def _make_card(self, title: str, subtitle: str = "") -> tuple[QFrame, QWidget]:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(8)
        head = QFrame()
        head.setObjectName("CardTitleBar")
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title.upper())
        title_label.setObjectName("CardTitle")
        head_lay.addWidget(title_label, 1)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("CardSubtitle")
            head_lay.addWidget(sub)
        lay.addWidget(head)
        body = QWidget()
        body.setObjectName("CardBody")
        lay.addWidget(body, 1)
        return card, body

    def _chip(self, text: str, command, primary: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("Primary" if primary else "Chip")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: command())
        return btn

    def _build_ui(self) -> None:
        self.backdrop = Backdrop()
        self.backdrop.set_bg(self._bg.get("mode", "aurora"), self._bg.get("value", ""))
        self.backdrop.set_gfx(self._gfx_full)
        self.setCentralWidget(self.backdrop)
        self.setStyleSheet(_build_qss())

        root_lay = QVBoxLayout(self.backdrop)
        root_lay.setContentsMargins(18, 14, 18, 14)
        root_lay.setSpacing(12)

        # ------------------------------------------------------- header
        header = QFrame()
        header.setObjectName("Header")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(16, 10, 16, 10)
        header_lay.setSpacing(8)

        brand = QLabel("JARVIS")
        brand_font = QFont("Segoe UI", 18)
        brand_font.setBold(True)
        brand.setFont(brand_font)
        brand.setStyleSheet(f"color: {CYAN};")
        header_lay.addWidget(brand)

        self.online_label = QLabel("STATUS: ONLINE")
        self.online_label.setStyleSheet(f"color: {GREEN}; font-weight: 700; font-size: 11px; padding: 4px 10px; background: {_rgba(STATUS, 200)}; border-radius: 8px;")
        header_lay.addWidget(self.online_label)

        header_lay.addStretch(1)

        for txt, cmd, tip in [
            ("▶", lambda: self._run_preset("youtube pause"), "Toggle YouTube play/pause"),
            ("⏭", lambda: self._run_preset("youtube next"), "Go to next YouTube video"),
            ("APPS", lambda: self._run_preset("list apps"), "Show apps in the launcher panel"),
        ]:
            b = self._chip(txt, cmd)
            b.setToolTip(tip)
            header_lay.addWidget(b)

        header_lay.addWidget(self._vsep())
        b = self._chip("MINI", self.mini.toggle)
        b.setToolTip("Show/hide the floating mini HUD")
        header_lay.addWidget(b)
        b = self._chip("DIAGNOSE", self._diagnose_system)
        b.setToolTip("Run full system diagnosis (CPU/RAM/GPU + process analysis)")
        header_lay.addWidget(b)
        b = self._chip("RECAP", self._recap)
        b.setToolTip("What happened while you were away")
        header_lay.addWidget(b)

        header_lay.addWidget(self._vsep())
        self.auto_start_button = self._chip("AUTOSTART", self._toggle_auto_start)
        self.auto_start_button.setToolTip("Toggle auto-start when this project opens / Windows login")
        header_lay.addWidget(self.auto_start_button)
        self.tray_button = self._chip("TRAY", self._toggle_tray)
        self.tray_button.setToolTip("Minimize to system tray / Ctrl+Shift+J summons the mini HUD")
        header_lay.addWidget(self.tray_button)
        self.gfx_button = self._chip("GFX", self._toggle_gfx)
        self.gfx_button.setToolTip("Toggle Lite / Full graphics effects")
        header_lay.addWidget(self.gfx_button)
        self.theme_button = self._chip(f"THEME {self._theme.upper()}", self._cycle_theme)
        self.theme_button.setToolTip("Cycle Aurora / Cyber / Light themes")
        header_lay.addWidget(self.theme_button)

        header_lay.addWidget(self._vsep())
        more_btn = self._chip("MORE ▾", self._show_more_menu)
        header_lay.addWidget(more_btn)
        self._more_menu = QMenu(self)
        self._more_menu.addAction("HELP  (F1)", self._open_help)
        self._more_menu.addAction("SCHEDULER", self._open_scheduler)
        self._more_menu.addAction("HISTORY", self._open_history)
        self._more_menu.addAction("TASKS", self._open_tasks)
        self._more_menu.addAction("MEMORY", self._open_memory)
        self._more_menu.addSeparator()
        self._more_menu.addAction("BACKGROUNDS", self._open_backgrounds)
        self._more_btn = more_btn

        root_lay.addWidget(header)

        # ------------------------------------------------------- body
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(12)

        cmd_card, cmd_body = self._make_card("Command Center", "Natural-language control")
        cmd_lay = QVBoxLayout(cmd_body)
        cmd_lay.setContentsMargins(0, 0, 0, 0)
        cmd_lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.command_entry = QLineEdit()
        self.command_entry.setPlaceholderText("Type a command, e.g. 'system status'…")
        self.command_entry.setFont(QFont("Consolas", 12))
        self.command_entry.returnPressed.connect(self._run_command)
        row.addWidget(self.command_entry, 1)
        self.run_button = self._chip("EXECUTE", self._run_command, primary=True)
        row.addWidget(self.run_button)
        cmd_lay.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for label, cmd_str in [
            ("system status", "system status"),
            ("metrics", "metrics"),
            ("list schedules", "list schedules"),
            ("job status", "job status"),
            ("time", "get current time"),
        ]:
            chips.addWidget(self._chip(label, lambda c=cmd_str: self._run_preset(c)))
        chips.addStretch(1)
        cmd_lay.addLayout(chips)
        left_lay.addWidget(cmd_card)

        feed_card, feed_body = self._make_card("Activity Feed", "Commands, agents and scheduler events")
        feed_lay = QVBoxLayout(feed_body)
        feed_lay.setContentsMargins(0, 0, 0, 0)
        feed_lay.setSpacing(6)
        feed_head = QHBoxLayout()
        feed_head.addStretch(1)
        clear_feed = self._chip("CLEAR", self._clear_feed)
        feed_head.addWidget(clear_feed)
        feed_lay.addLayout(feed_head)
        self.feed_text = QTextEdit()
        self.feed_text.setReadOnly(True)
        self.feed_text.setMaximumHeight(150)
        feed_lay.addWidget(self.feed_text)
        left_lay.addWidget(feed_card, 1)

        term_card, term_body = self._make_card("Live Terminal", "Command, agent and scheduler logs")
        term_lay = QVBoxLayout(term_body)
        term_lay.setContentsMargins(0, 0, 0, 0)
        term_lay.setSpacing(6)
        term_head = QHBoxLayout()
        label = QLabel("ALL EVENTS STREAM HERE")
        label.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        term_head.addWidget(label, 1)
        term_head.addWidget(self._chip("CLEAR", self._clear_terminal))
        term_lay.addLayout(term_head)
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        term_lay.addWidget(self.terminal, 1)
        left_lay.addWidget(term_card, 2)
        splitter.addWidget(left)

        # ------------------------------------------------------- AI assistant
        ai_card, ai_body = self._make_card("AI Assistant", "Jarvis plans, executes and streams answers")
        ai_lay = QVBoxLayout(ai_body)
        ai_lay.setContentsMargins(0, 0, 0, 0)
        ai_lay.setSpacing(8)
        self.chat_text = QTextEdit()
        self.chat_text.setReadOnly(True)
        self.chat_text.setFont(QFont("Consolas", 11))
        ai_lay.addWidget(self.chat_text, 1)
        chat_inp = QHBoxLayout()
        chat_inp.setSpacing(8)
        self.chat_entry = QLineEdit()
        self.chat_entry.setPlaceholderText("Ask JARVIS anything…")
        self.chat_entry.returnPressed.connect(self._ask_chatbot)
        chat_inp.addWidget(self.chat_entry, 1)
        chat_inp.addWidget(self._chip("ASK", self._ask_chatbot, primary=True))
        ai_lay.addLayout(chat_inp)
        splitter.addWidget(ai_card)

        # ------------------------------------------------------- right column
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(12)

        voice_card, voice_body = self._make_card("Voice Engine", "Click the wave to toggle")
        voice_lay = QVBoxLayout(voice_body)
        voice_lay.setContentsMargins(0, 0, 0, 0)
        voice_lay.setSpacing(8)
        self.wave_canvas = WaveWidget(self._toggle_voice)
        voice_lay.addWidget(self.wave_canvas)
        self.voice_switch_button = self._chip("VOICE OFF", self._toggle_voice)
        voice_lay.addWidget(self.voice_switch_button)
        right_lay.addWidget(voice_card)

        job_card, job_body = self._make_card("Job Agent", "Autonomous job-application hunter")
        job_lay = QVBoxLayout(job_body)
        job_lay.setContentsMargins(0, 0, 0, 0)
        job_lay.setSpacing(8)
        self.job_status_label = QLabel("No job-application task running.")
        self.job_status_label.setWordWrap(True)
        self.job_status_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        job_lay.addWidget(self.job_status_label)
        job_stats = QHBoxLayout()
        self.job_stat_labels: dict[str, QLabel] = {}
        stat_colors = {
            "APPLIED": GREEN, "SKIPPED": AMBER, "FAILED": RED,
            "INTERVIEW": CYAN, "OFFER": GREEN, "REJECTED": RED,
        }
        for name in ("APPLIED", "SKIPPED", "FAILED", "INTERVIEW", "OFFER", "REJECTED"):
            lbl = QLabel(f"{name} 0")
            lbl.setStyleSheet(f"color: {stat_colors[name]}; font-weight: 700; font-size: 11px;")
            job_stats.addWidget(lbl)
            self.job_stat_labels[name] = lbl
        job_lay.addLayout(job_stats)
        self.job_progress = QProgressBar()
        self.job_progress.setMaximum(100)
        self.job_progress.setValue(0)
        job_lay.addWidget(self.job_progress)
        qrow = QHBoxLayout()
        qrow.setSpacing(6)
        self.job_query_entry = QLineEdit()
        self.job_query_entry.setPlaceholderText("role query…")
        self.job_query_entry.returnPressed.connect(self._start_job_apply)
        qrow.addWidget(self.job_query_entry, 1)
        qrow.addWidget(self._chip("APPLY", self._start_job_apply, primary=True))
        job_lay.addLayout(qrow)
        ctrl = QHBoxLayout()
        ctrl.addWidget(self._chip("STATUS", self._refresh_job_status))
        ctrl.addWidget(self._chip("CANCEL", self._cancel_job))
        ctrl.addWidget(self._chip("RESUME", self._resume_job))
        ctrl.addStretch(1)
        job_lay.addLayout(ctrl)
        recent_label = QLabel("RECENT APPLICATIONS")
        recent_label.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700;")
        job_lay.addWidget(recent_label)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(110)
        job_lay.addWidget(self.recent_list)
        right_lay.addWidget(job_card)

        agent_card, agent_body = self._make_card("Agent Status", "Pending approval requests")
        agent_lay = QVBoxLayout(agent_body)
        agent_lay.setContentsMargins(0, 0, 0, 0)
        agent_lay.setSpacing(8)
        agent_head = QHBoxLayout()
        lbl = QLabel("PENDING APPROVALS")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700;")
        agent_head.addWidget(lbl, 1)
        self._approval_badge = QLabel("0")
        self._approval_badge.setStyleSheet(f"color: {TEXT}; background: {_rgba(BTN2, 220)}; padding: 2px 8px; border-radius: 8px; font-weight: 700;")
        agent_head.addWidget(self._approval_badge)
        agent_lay.addLayout(agent_head)
        self.approval_scroll = QScrollArea()
        self.approval_scroll.setWidgetResizable(True)
        self.approval_scroll.setMaximumHeight(190)
        self.approval_inner = QWidget()
        self.approval_lay = QVBoxLayout(self.approval_inner)
        self.approval_lay.setContentsMargins(0, 0, 0, 0)
        self.approval_lay.setSpacing(6)
        self.approval_lay.addStretch(1)
        self.approval_scroll.setWidget(self.approval_inner)
        agent_lay.addWidget(self.approval_scroll)
        right_lay.addWidget(agent_card)

        apps_card, apps_body = self._make_card("Available Apps", "Alt+1..9 to launch")
        apps_lay = QVBoxLayout(apps_body)
        apps_lay.setContentsMargins(0, 0, 0, 0)
        apps_lay.setSpacing(8)
        self.app_filter_var = QLineEdit()
        self.app_filter_var.setPlaceholderText("Filter apps…")
        self.app_filter_var.textChanged.connect(self._apply_app_filter)
        apps_lay.addWidget(self.app_filter_var)
        self.app_listbox = QListWidget()
        self.app_listbox.itemDoubleClicked.connect(lambda _: self._open_selected_app())
        apps_lay.addWidget(self.app_listbox)
        right_lay.addWidget(apps_card)

        right_lay.addStretch(1)
        right_scroll.setWidget(right)
        splitter.addWidget(right_scroll)

        splitter.setSizes([560, 420, 380])
        root_lay.addWidget(splitter, 1)

        # ------------------------------------------------------- status bar
        status = QFrame()
        status.setObjectName("Header")
        status_lay = QHBoxLayout(status)
        status_lay.setContentsMargins(16, 6, 16, 6)
        status_lay.setSpacing(12)
        self.cpu_status = QLabel("CPU --%")
        self.ram_status = QLabel("RAM --%")
        self.net_status = QLabel("NET --/--")
        self.status_hint = QLabel("Say 'jarvis' + your command · Ctrl+Shift+J summons mini HUD")
        self.status_hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        for w in (self.cpu_status, self.ram_status, self.net_status):
            w.setStyleSheet(f"color: {TEXT}; font-size: 11px; font-weight: 600;")
            status_lay.addWidget(w)
        status_lay.addStretch(1)
        status_lay.addWidget(self.status_hint)
        root_lay.addWidget(status)

        self._refresh_status_meters()

    def _vsep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet(f"color: {_rgba(STROKE, 160)}; background: {_rgba(STROKE, 160)};")
        f.setFixedWidth(2)
        return f

    def _show_more_menu(self) -> None:
        self._more_menu.exec(self._more_btn.mapToGlobal(self._more_btn.rect().bottomLeft()))

    def _refresh_status_meters(self) -> None:
        try:
            snap = self._monitor.snapshot()
            self.cpu_status.setText(f"CPU {snap.cpu_percent:.0f}%")
            self.ram_status.setText(f"RAM {snap.memory_percent:.0f}%")
            self.net_status.setText(f"NET ↓{snap.net_recv_kbps:.0f} ↑{snap.net_sent_kbps:.0f} kbps")
        except Exception:
            pass

    def _post_build(self) -> None:
        self._refresh_voice_button()
        self._refresh_auto_start_button()
        self._refresh_gfx_button()
        self._refresh_approvals()
        self._refresh_app_launcher()
        self._bind_hotkeys()
        self._refresh_job_status()

    # ------------------------------------------------------------ theme / gfx / bg

    def _rebuild_ui(self) -> None:
        self.setStyleSheet(_build_qss())
        self.backdrop.set_bg(self._bg.get("mode", "aurora"), self._bg.get("value", ""))
        self.backdrop.set_gfx(self._gfx_full)
        self._refresh_gfx_button()

    def _cycle_theme(self) -> None:
        idx = THEME_ORDER.index(_current_theme)
        self._theme = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        _apply_palette(self._theme)
        self.theme_button.setText(f"THEME {self._theme.upper()}")
        self._rebuild_ui()
        self._save_prefs()

    def _toggle_gfx(self) -> None:
        self._gfx_full = not self._gfx_full
        self.backdrop.set_gfx(self._gfx_full)
        self._refresh_gfx_button()
        self._save_prefs()

    def _refresh_gfx_button(self) -> None:
        self.gfx_button.setText("GFX FULL" if self._gfx_full else "GFX LITE")
        if self._gfx_full:
            self.gfx_button.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
        else:
            self.gfx_button.setStyleSheet("")

    def _apply_background(self, mode: str, value: str = "") -> None:
        self._bg = {"mode": mode, "value": value}
        self.backdrop.set_bg(mode, value)
        self._save_prefs()

    def _open_backgrounds(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — Backgrounds")
        win.resize(640, 420)
        lay = QVBoxLayout(win)

        head = QHBoxLayout()
        title = QLabel("BACKGROUND")
        title.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: 800;")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        mode_row = QHBoxLayout()
        current_mode = self._bg.get("mode", "aurora")
        for name, label in [("aurora", "Aurora scene"), ("gradient", "Gradient"), ("image", "Static image")]:
            btn = self._chip(label, lambda m=name: self._apply_background(m))
            if current_mode == name:
                btn.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
            mode_row.addWidget(btn)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)

        if current_mode == "gradient":
            grid = QHBoxLayout()
            for i, (_name, top, bottom) in enumerate(Backdrop.GRADIENTS):
                sw = QLabel()
                pix = QPixmap(120, 60)
                p = QPainter(pix)
                grad = QLinearGradient(0, 0, 0, 60)
                grad.setColorAt(0, QColor(top))
                grad.setColorAt(1, QColor(bottom))
                p.fillRect(0, 0, 120, 60, grad)
                p.end()
                sw.setPixmap(pix)
                sw.setCursor(Qt.CursorShape.PointingHandCursor)
                sw.mousePressEvent = lambda _e, idx=i: self._apply_background("gradient", str(idx))
                grid.addWidget(sw)
            body_lay.addLayout(grid)
        elif current_mode == "image":
            browse = self._chip("BROWSE…", lambda: self._pick_background_image(win))
            body_lay.addWidget(browse)
            files = list_background_images(_backgrounds_dir())
            if not files:
                hint = QLabel("No images yet — pick one above, or drop a .png/.gif in data/backgrounds/.")
                hint.setStyleSheet(f"color: {MUTED};")
                body_lay.addWidget(hint)
            grid = QHBoxLayout()
            for item in files:
                btn = self._chip(item["name"], lambda n=item["name"]: self._apply_background("image", n))
                if self._bg.get("value") == item["name"]:
                    btn.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
                grid.addWidget(btn)
            body_lay.addLayout(grid)
        else:
            hint = QLabel("Animated aurora scene with drifting particles. Toggle GFX for more/fewer particles.")
            hint.setStyleSheet(f"color: {MUTED};")
            body_lay.addWidget(hint)

        lay.addWidget(body, 1)
        done = self._chip("DONE", win.accept, primary=True)
        lay.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        win.exec()

    def _pick_background_image(self, win: QDialog) -> None:
        path, _ = QFileDialog.getOpenFileName(
            win,
            "Choose a background image",
            "",
            "Image files (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*.*)",
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
            win.close()
        except Exception:
            self._toaster.notify("Background import failed", "Could not import that image.", RED)

    # ------------------------------------------------------------ terminal / feed

    def _append_terminal(self, kind: str, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        tag = {"CMD": CYAN, "OK": GREEN, "WARN": AMBER, "ERR": RED, "SYS": INDIGO}.get(kind, MUTED)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.terminal.append(
            f'<span style="color:{MUTED}">[{ts}]</span> <span style="color:{tag}">{safe}</span>'
        )
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def _append_feed(self, tag: str, text: str) -> None:
        color = {
            "feed_cmd": CYAN, "feed_ok": GREEN, "feed_pending": AMBER,
            "feed_err": RED, "feed_sys": MUTED,
        }.get(tag, MUTED)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.feed_text.append(f'<span style="color:{color}">{safe}</span>')
        self.feed_text.verticalScrollBar().setValue(self.feed_text.verticalScrollBar().maximum())

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.run_button.setEnabled(not busy)

    def _clear_terminal(self) -> None:
        self.terminal.clear()

    def _clear_feed(self) -> None:
        self.feed_text.clear()

    # ------------------------------------------------------------ commands

    def _run_preset(self, command: str) -> None:
        self.command_entry.setText(command)
        self._run_command()

    def _run_command(self) -> None:
        if self._busy:
            return
        command = self.command_entry.text().strip()
        if not command:
            return
        self.command_entry.clear()
        self._run_command_text(command)

    def _run_mini_command(self, command: str) -> None:
        self._run_command_text(command)

    def _run_command_text(self, command: str) -> None:
        if not command or self._busy:
            return
        self._push_history(command)
        self._append_terminal("CMD", f"> {command}")
        self._append_feed("feed_cmd", f"▶ {command}")
        self.mini.notify_command(command)
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
        self.command_entry.setText(self._cmd_history[idx])

    # ------------------------------------------------------------ voice

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
        running = self.runtime.voice.is_running()
        try:
            status = self.runtime.voice.status()
        except Exception:
            status = {}
        phase, color, amp, speed = voice_phase(status)
        self.wave_canvas.update_status(phase, color, amp, speed)
        if running:
            self.voice_switch_button.setText("VOICE ON")
            self.voice_switch_button.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
            self.online_label.setText("STATUS: STREAMING")
            self.online_label.setStyleSheet(f"color: {CYAN}; font-weight: 700; font-size: 11px; padding: 4px 10px; background: {_rgba(STATUS, 200)}; border-radius: 8px;")
        else:
            self.voice_switch_button.setText("VOICE OFF")
            self.voice_switch_button.setStyleSheet("")
            self.online_label.setText("STATUS: ONLINE")
            self.online_label.setStyleSheet(f"color: {GREEN}; font-weight: 700; font-size: 11px; padding: 4px 10px; background: {_rgba(STATUS, 200)}; border-radius: 8px;")

    # ------------------------------------------------------------ job agent

    def _start_job_apply(self) -> None:
        query = self.job_query_entry.text().strip()
        if not query:
            return
        self.job_query_entry.clear()
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
        self._refresh_job_status()

    def _update_job_panel(self, payload: dict[str, Any]) -> None:
        task = payload.get("task") or {}
        status = str(task.get("status") or "idle")
        phase = str(task.get("phase") or "-")
        applied = int(task.get("applied") or 0)
        skipped = int(task.get("skipped") or 0)
        failed = int(task.get("failed") or 0)
        reason = str(task.get("paused_reason") or "").strip()
        color = {"idle": MUTED, "running": CYAN, "paused": AMBER, "completed": GREEN, "canceled": MUTED}.get(status, MUTED)
        text = f"[{status.upper()} · {phase}]"
        if reason:
            text += f"\nPaused: {reason}"
        self.job_status_label.setText(text)
        self.job_status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.job_stat_labels["APPLIED"].setText(f"APPLIED {applied}")
        self.job_stat_labels["SKIPPED"].setText(f"SKIPPED {skipped}")
        self.job_stat_labels["FAILED"].setText(f"FAILED {failed}")
        totals = payload.get("totals") or (task.get("totals") if isinstance(task, dict) else None) or {}
        for name in ("INTERVIEW", "OFFER", "REJECTED"):
            self.job_stat_labels[name].setText(f"{name} {int(totals.get(name.lower(), 0))}")
        try:
            max_apps = int(task.get("max_applications") or 0)
            pct = round((applied / max_apps) * 100) if max_apps else 0
            self.job_progress.setValue(max(0, min(100, pct)))
        except Exception:
            self.job_progress.setValue(0)
        recent = payload.get("recent") or []
        self.recent_list.clear()
        for item in recent[:12]:
            title = str(item.get("title") or "?")
            company = str(item.get("company") or item.get("site") or "?")
            ts = str(item.get("created_at") or "")[:16].replace("T", " ")
            self.recent_list.addItem(f"{ts}  {title} @ {company}")
        if status == "paused" and self._job_prev_status != "paused":
            self._toaster.notify("Job paused", reason or "Task paused, waiting on you.", AMBER, on_click=self._raise_main_window)
        self._job_prev_status = status

    # ------------------------------------------------------------ approvals

    def _refresh_approvals(self) -> None:
        def worker() -> None:
            try:
                items = [i.model_dump() for i in self.runtime.orchestrator.list_pending_approvals()]
                self._events.put(("approvals", items))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _render_approval_cards(self, items: list[dict[str, Any]]) -> None:
        while self.approval_lay.count():
            item = self.approval_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._approval_cards.clear()
        self._pending_ids = [str(item.get("id") or item.get("approval_id") or "") for item in items]
        for item in items:
            card = QFrame()
            card.setObjectName("Card")
            card.setStyleSheet(f"background: {_rgba(INNER, 200)}; border: 1px solid {_rgba(STROKE, 180)}; border-radius: 10px;")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(4)
            prompt = str(item.get("prompt") or item.get("command") or "")
            p = QLabel(prompt[:120] or "(no prompt)")
            p.setWordWrap(True)
            p.setStyleSheet(f"color: {TEXT}; font-size: 11px; font-weight: 700;")
            lay.addWidget(p)
            detail = str(item.get("reason") or item.get("summary") or item.get("description") or "")
            if detail:
                d = QLabel(detail[:160])
                d.setWordWrap(True)
                d.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
                lay.addWidget(d)
            act = QHBoxLayout()
            approval_id = str(item.get("id") or item.get("approval_id") or "")
            approve = self._chip("APPROVE", lambda aid=approval_id: self._approve_id(aid))
            approve.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
            reject = self._chip("REJECT", lambda aid=approval_id: self._reject_id(aid))
            reject.setStyleSheet(f"color: {RED}; background: {_rgba(DANGER, 220)};")
            act.addWidget(approve)
            act.addWidget(reject)
            act.addStretch(1)
            lay.addLayout(act)
            self.approval_lay.addWidget(card)
            self._approval_cards[approval_id] = card
        self.approval_lay.addStretch(1)
        count = len(items)
        self._approval_badge.setText(str(count) if count else "0")
        self._approval_badge.setStyleSheet(
            f"color: {'#021018' if count else TEXT}; background: {_rgba(AMBER if count else BTN2, 220)}; padding: 2px 8px; border-radius: 8px; font-weight: 700;"
        )
        if len(items) > self._last_pending_count and items:
            self._toaster.notify("Approval pending", f"{len(items)} action(s) waiting for you.", AMBER, on_click=self._raise_main_window)
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

    # ------------------------------------------------------------ diagnose

    def _diagnose_system(self) -> None:
        self._append_terminal("SYS", "Running system diagnosis…")

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
                self._append_terminal("SYS", f"Diagnosis: issue detected — {diagnosis['root_cause']}")
            else:
                self._append_terminal("SYS", f"Diagnosis: no issues detected — {explanation}")
            if raw_metrics:
                cpu = raw_metrics.get("cpu_percent", "N/A")
                mem = raw_metrics.get("memory_percent", "N/A")
                self._append_terminal("SYS", f"  CPU: {cpu}% • RAM: {mem}%")
        else:
            self._append_terminal("WARN", f"Diagnosis not available: {payload.get('explanation', 'unknown')}")

    def _handle_diagnosis_error(self, message: str) -> None:
        self._append_terminal("ERR", f"Diagnosis failed: {message}")

    # ------------------------------------------------------------ hub / events

    def _on_hub_event(self, topic: str, payload: dict[str, Any]) -> None:
        if topic in {"command_start", "command_done", "agent_step", "approval", "scheduler_run"}:
            self._events.put(("hub_event", (topic, payload)))

    def _handle_hub_event(self, topic: str, payload: dict[str, Any]) -> None:
        text = ""
        tag = "feed_sys"
        if topic == "command_start":
            text = f"▸ {payload.get('command', '')}"
            tag = "feed_cmd"
        elif topic == "command_done":
            status = payload.get("status", "done")
            text = f"✓ {payload.get('command', '')} → {status}"
            tag = "feed_ok" if str(status) not in {"error", "failed"} else "feed_err"
        elif topic == "agent_step":
            text = f"⚙ {payload.get('step', '')}"
            tag = "feed_pending"
        elif topic == "approval":
            self._refresh_approvals()
        elif topic == "scheduler_run":
            text = f"⏱ scheduled: {payload.get('command', '')} [{payload.get('status', '')}]"
        if text:
            self._append_feed(tag, text)

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
            self._append_feed("feed_err", f"✗ {command} FAILED")
            self._toaster.notify("Command failed", command, RED)
        elif has_pending:
            self._append_feed("feed_pending", f"⏳ {command} awaiting approval")
            self._toaster.notify("Approval required", command, AMBER, on_click=self._raise_main_window)
        else:
            self._append_feed("feed_ok", f"✓ {command} done")
        self._refresh_approvals()

    def _handle_agent_step(self, step: dict[str, Any]) -> None:
        tool = str(step.get("tool", "?"))
        status = str(step.get("status", "?"))
        iteration = int(step.get("iteration", 1))
        snippet = str(step.get("output_snippet", "") or "").strip()
        line = f"[agent i{iteration}] ▸ {tool} [{status}]"
        if snippet:
            line += f" — {snippet}"
        if status == "failed":
            self._append_terminal("ERR", line)
            self._append_feed("feed_err", f"✗ agent: {tool} failed")
        elif status == "pending":
            self._append_terminal("WARN", line)
            self._append_feed("feed_pending", f"⏳ agent: {tool} pending approval")
        else:
            self._append_terminal("OK", line)
            self._append_feed("feed_cmd", f"▸ agent: {tool} done")

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
                self._refresh_voice_button()
            elif event == "approvals":
                self._render_approval_cards(payload)
            elif event == "hub_event":
                topic, payload2 = payload
                self._handle_hub_event(topic, payload2)
            elif event == "summon_mini":
                self._raise_main_window()
                self.mini.show()
                self._toaster.notify("JARVIS", "Mini HUD summoned — Ctrl+Shift+J", CYAN, duration_ms=2500)
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
            elif event == "sched_run":
                job, status, text = payload
                tag = "OK" if str(status) != "failed" else "ERR"
                self._append_terminal(tag, f"[scheduler] {job.get('command')} → {status}: {text}")
                self._append_feed("feed_sys", f"⏱ scheduled: {job.get('command')} [{status}]")
            elif event == "sched_list":
                self._handle_sched_list(payload)
            elif event == "sched_added":
                self._append_terminal("OK", f"[scheduler] added: {payload}")
                self._append_feed("feed_sys", f"⏱ scheduled: {payload}")
            elif event == "sched_error":
                self._append_terminal("ERR", f"[scheduler] {payload}")
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
                self._toaster.notify("JARVIS error", str(payload), RED)

    # ------------------------------------------------------------ app launcher

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
        q = self.app_filter_var.text().strip().lower()
        self.app_listbox.clear()
        self._app_launch_items = []
        for item in self._all_app_launch_items:
            if q and q not in item["label"].lower() and q not in item["command"].lower():
                continue
            self._app_launch_items.append(item)
        for idx, item in enumerate(self._app_launch_items):
            hk = f"Alt+{idx+1}" if idx < 9 else "      "
            self.app_listbox.addItem(f"{hk:<7}  {item['label']}")
        if not self._app_launch_items:
            self.app_listbox.addItem("No apps match filter.")

    def _open_selected_app(self) -> None:
        row = self.app_listbox.currentRow()
        if row >= 0:
            self._open_app_by_index(row)

    def _open_app_by_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._app_launch_items):
            return
        self.command_entry.setText(self._app_launch_items[idx]["command"])
        self._run_command()

    # ------------------------------------------------------------ chat

    def _append_chat(self, role: str, text: str) -> None:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if role == "user":
            self.chat_text.append(f'<span style="color:{CYAN}; font-weight:700; font-size:10px;">YOU</span>')
            self.chat_text.append(f'<span style="color:{TEXT}">{safe}</span>')
        else:
            self.chat_text.append(f'<span style="color:{INDIGO}; font-weight:700; font-size:10px;">JARVIS</span>')
            self.chat_text.append(f'<span style="color:{TEXT}">{safe}</span>')
        self.chat_text.verticalScrollBar().setValue(self.chat_text.verticalScrollBar().maximum())

    def _start_chat_stream(self) -> None:
        self.chat_text.append(f'<span style="color:{INDIGO}; font-weight:700; font-size:10px;">JARVIS</span>')
        self._chat_streaming = True

    def _append_chat_stream_chunk(self, chunk: str) -> None:
        if not self._chat_streaming:
            return
        safe = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        cursor = self.chat_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(f'<span style="color:{TEXT}">{safe}</span>')
        self.chat_text.setTextCursor(cursor)
        self.chat_text.verticalScrollBar().setValue(self.chat_text.verticalScrollBar().maximum())

    def _finish_chat_stream(self) -> None:
        if not self._chat_streaming:
            return
        self.chat_text.append("")
        self._chat_streaming = False

    def _ask_chatbot(self) -> None:
        q = self.chat_entry.text().strip()
        if not q:
            return
        self.chat_entry.clear()
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

    # ------------------------------------------------------------ hotkeys

    def _bind_hotkeys(self) -> None:
        for i in range(1, 10):
            sc = QShortcut(QKeySequence(f"Alt+{i}"), self)
            sc.activated.connect(lambda idx=i - 1: self._open_app_by_index(idx))
        sc = QShortcut(QKeySequence("F1"), self)
        sc.activated.connect(self._open_help)
        sc = QShortcut(QKeySequence("Ctrl+L"), self)
        sc.activated.connect(self._clear_terminal)
        sc = QShortcut(QKeySequence("Ctrl+K"), self)
        sc.activated.connect(self._clear_feed)

    # ------------------------------------------------------------ scheduler

    def _scheduler_on_run(self, job: dict[str, Any], status: str, text: str) -> None:
        self._events.put(("sched_run", (job, status, text)))
        try:
            self._hub.publish(
                "scheduler_run",
                {"command": job.get("command"), "status": status, "result": text, "job_id": job.get("id")},
            )
        except Exception:
            pass

    def _open_scheduler(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — Scheduler")
        win.resize(760, 520)
        lay = QVBoxLayout(win)

        add_panel = QFrame()
        add_panel.setObjectName("Card")
        add_lay = QVBoxLayout(add_panel)
        add_lay.setContentsMargins(12, 10, 12, 10)
        add_lay.setSpacing(6)
        lbl = QLabel("NEW SCHEDULE")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700;")
        add_lay.addWidget(lbl)
        row1 = QHBoxLayout()
        cmd_entry = QLineEdit()
        cmd_entry.setPlaceholderText("command…")
        rec_entry = QLineEdit()
        rec_entry.setPlaceholderText("recurrence (e.g. daily at 09:00)…")
        row1.addWidget(cmd_entry, 1)
        row1.addWidget(rec_entry, 1)
        add_lay.addLayout(row1)
        hint = QLabel("e.g. daily at 09:00  ·  hourly  ·  every 30 minutes  ·  weekly on monday at 10:00")
        hint.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        add_lay.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._chip("SCHEDULE", lambda: self._add_schedule(cmd_entry, rec_entry), primary=True))
        add_lay.addLayout(btn_row)
        lay.addWidget(add_panel)

        list_panel = QFrame()
        list_panel.setObjectName("Card")
        list_lay = QVBoxLayout(list_panel)
        list_lay.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        lbl = QLabel("SCHEDULED JOBS")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700;")
        head.addWidget(lbl, 1)
        head.addWidget(self._chip("REFRESH", self._scheduler_load))
        list_lay.addLayout(head)
        self._sched_listbox = QListWidget()
        list_lay.addWidget(self._sched_listbox)
        lay.addWidget(list_panel, 1)

        actions = QHBoxLayout()
        self._sched_status = QLabel("")
        self._sched_status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        actions.addWidget(self._sched_status, 1)
        actions.addWidget(self._chip("ENABLE / DISABLE", self._toggle_schedule))
        actions.addWidget(self._chip("DELETE", self._delete_schedule))
        lay.addLayout(actions)

        self._sched_win = win
        self._sched_jobs: list[dict[str, Any]] = []
        self._scheduler_load()
        poll = QTimer(win)
        poll.timeout.connect(self._scheduler_load)
        poll.start(4000)
        win.exec()

    def _scheduler_load(self) -> None:
        def worker() -> None:
            try:
                jobs = self.runtime.scheduler.list_jobs()
                self._events.put(("sched_list", jobs))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_sched_list(self, jobs: list[dict[str, Any]]) -> None:
        if not getattr(self, "_sched_win", None):
            return
        self._sched_jobs = jobs
        box = self._sched_listbox
        box.clear()
        if not jobs:
            box.addItem("No schedules yet.")
        for job in jobs:
            state = "ON " if job.get("enabled") else "OFF"
            last = job.get("last_status") or "never"
            stamp = str(job.get("last_result") or "")[:60]
            box.addItem(f"[{state}] {job.get('id', '')}  {job.get('command', '')} — {job.get('recurrence', '')}  |  last: {last}{' · ' + stamp if stamp else ''}")
        self._sched_status.setText(f"{len(jobs)} job(s) · auto-refreshes every 4s")

    def _add_schedule(self, cmd_entry: QLineEdit, rec_entry: QLineEdit) -> None:
        command = cmd_entry.text().strip()
        recurrence = rec_entry.text().strip()
        if not command or not recurrence:
            return

        def worker() -> None:
            try:
                job = self.runtime.scheduler.add(command=command, recurrence=recurrence)
                self._events.put(("sched_added", job.get("id", "")))
                self._events.put(("sched_list", self.runtime.scheduler.list_jobs()))
            except Exception as exc:
                self._events.put(("sched_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _delete_schedule(self) -> None:
        row = self._sched_listbox.currentRow()
        if row < 0 or row >= len(self._sched_jobs):
            return
        job_id = str(self._sched_jobs[row].get("id", ""))
        if not job_id:
            return

        def worker() -> None:
            try:
                self.runtime.scheduler.remove(job_id)
                self._events.put(("sched_list", self.runtime.scheduler.list_jobs()))
            except Exception as exc:
                self._events.put(("sched_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_schedule(self) -> None:
        row = self._sched_listbox.currentRow()
        if row < 0 or row >= len(self._sched_jobs):
            return
        job = self._sched_jobs[row]
        job_id = str(job.get("id", ""))

        def worker() -> None:
            try:
                if job.get("enabled"):
                    self.runtime.scheduler.set_enabled(job_id, False)
                else:
                    self.runtime.scheduler.set_enabled(job_id, True)
                self._events.put(("sched_list", self.runtime.scheduler.list_jobs()))
            except Exception as exc:
                self._events.put(("sched_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ history

    def _open_history(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — History")
        win.resize(820, 560)
        lay = QVBoxLayout(win)
        tabs = QTabWidget()
        self._hist_texts: dict[str, QTextEdit] = {}
        for tab_name in ("commands", "tools", "metrics"):
            text = QTextEdit()
            text.setReadOnly(True)
            tabs.addTab(text, tab_name.upper())
            self._hist_texts[tab_name] = text
        lay.addWidget(tabs, 1)
        status = QLabel("")
        status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(status)
        self._hist_status = status

        self._hist_win = win
        self._history_load()
        win.exec()

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

    def _fill_history_tab(self, key: str, lines: list[str]) -> None:
        text = self._hist_texts.get(key)
        if text is None:
            return
        text.clear()
        text.setPlainText("\n".join(lines))

    def _handle_history_data(self, data: dict[str, Any]) -> None:
        if not getattr(self, "_hist_win", None):
            return
        cmd_lines: list[str] = []
        for row in (data.get("commands") or [])[:60]:
            cmd_lines.append(f"[{row.get('created_at', '')[:19]}] {row.get('source', '?')}: {row.get('command', '')}")
            snippet = str(row.get("response_text") or "")[:160].replace("\n", " ")
            if snippet:
                cmd_lines.append(f"      {snippet}")
        self._fill_history_tab("commands", cmd_lines or ["No command history yet."])

        tool_lines: list[str] = []
        for row in (data.get("tools") or [])[:80]:
            tool_lines.append(f"[{row.get('created_at', '')[:19]}] {row.get('tool', '?')} [{row.get('status', '?')}]")
            err = str(row.get("error") or "") if row.get("error") else ""
            tool_lines.append(f"      {row.get('command', '')}  ·  risk: {row.get('risk', 'low')}{'  ·  ' + err if err else ''}")
        self._fill_history_tab("tools", tool_lines or ["No tool activity yet."])

        metrics = data.get("metrics") or {}
        summary = metrics.get("summary") or {}
        m_lines = [
            "METRICS SUMMARY",
            f"  window: {summary.get('window', 0)} executions",
            f"  total commands: {summary.get('total_commands', 0)}",
            f"  success rate: {float(summary.get('success_rate', 0)) * 100:.1f}%",
            f"  failure rate: {float(summary.get('failure_rate', 0)) * 100:.1f}%",
            f"  avg duration: {int(summary.get('avg_duration_ms', 0))} ms",
        ]
        status_counts = summary.get("status_counts") or {}
        if status_counts:
            m_lines.append("")
            m_lines.append("BY STATUS")
            for status, count in status_counts.items():
                m_lines.append(f"  {status}: {count}")
        recent = (metrics.get("recent") or [])[:30]
        if recent:
            m_lines.append("")
            m_lines.append("RECENT EXECUTIONS")
            for row in recent:
                m_lines.append(f"  [{row.get('created_at', '')[:19]}] {row.get('command', '')} → {row.get('status', '?')} ({int(row.get('duration_ms', 0))}ms)")
        self._fill_history_tab("metrics", m_lines)
        self._hist_status.setText(f"Loaded at {datetime.now().strftime('%H:%M:%S')}")

    # ------------------------------------------------------------ tasks

    def _open_tasks(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — Tasks & Progress")
        win.resize(840, 580)
        lay = QVBoxLayout(win)
        head = QHBoxLayout()
        title = QLabel("TASKS & PROGRESS")
        title.setStyleSheet(f"color: {CYAN}; font-size: 14px; font-weight: 800;")
        head.addWidget(title, 1)
        head.addWidget(self._chip("REFRESH", self._tasks_load))
        lay.addLayout(head)

        list_panel = QFrame()
        list_panel.setObjectName("Card")
        list_lay = QVBoxLayout(list_panel)
        lbl = QLabel("TRACKED TASKS")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700;")
        list_lay.addWidget(lbl)
        self._tasks_listbox = QListWidget()
        self._tasks_listbox.currentRowChanged.connect(lambda _: self._show_task_detail())
        list_lay.addWidget(self._tasks_listbox)
        lay.addWidget(list_panel, 1)

        detail_panel = QFrame()
        detail_panel.setObjectName("Card")
        detail_lay = QVBoxLayout(detail_panel)
        lbl = QLabel("TASK DETAIL")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700;")
        detail_lay.addWidget(lbl)
        self._task_detail_text = QTextEdit()
        self._task_detail_text.setReadOnly(True)
        detail_lay.addWidget(self._task_detail_text)
        lay.addWidget(detail_panel, 2)

        status = QLabel("")
        status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(status)
        self._tasks_status = status

        self._tasks_win = win
        self._tasks_items: list[dict[str, Any]] = []
        self._tasks_load()
        poll = QTimer(win)
        poll.timeout.connect(self._tasks_load)
        poll.start(4000)
        win.exec()

    def _tasks_load(self) -> None:
        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("task_list", {"limit": 50})
                self._events.put(("tasks_list", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_tasks_list(self, payload: dict[str, Any]) -> None:
        if not getattr(self, "_tasks_win", None):
            return
        items = payload.get("items") or []
        self._tasks_items = items
        box = self._tasks_listbox
        box.clear()
        if not items:
            box.addItem("No tasks yet — ask JARVIS to 'plan' something, or create one via the planner.")
        for item in items:
            progress = item.get("progress") or {}
            status = str(item.get("status", "?")).upper()
            box.addItem(f"[{status}] {item.get('id', '')}  {str(item.get('command', ''))[:48]}  |  {progress.get('completed', 0)}/{progress.get('total', 0)} steps ({progress.get('percent', 0)}%)")
        self._tasks_status.setText(f"{len(items)} task(s) · auto-refreshes every 4s")

    def _show_task_detail(self) -> None:
        row = self._tasks_listbox.currentRow()
        if row < 0 or row >= len(self._tasks_items):
            return
        task_id = str(self._tasks_items[row].get("id", ""))

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("task_status", {"task_id": task_id})
                self._events.put(("task_detail", payload))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_task_detail(self, payload: dict[str, Any]) -> None:
        text = getattr(self, "_task_detail_text", None)
        if text is None:
            return
        lines = [
            f"TASK {payload.get('task_id', '?')}",
            f"  command: {payload.get('command', '')}",
            f"  status: {payload.get('status', '?')}",
        ]
        progress = payload.get("progress") or {}
        lines.append(f"  progress: {progress.get('completed', 0)}/{progress.get('total', 0)} steps ({progress.get('percent', 0)}%), current: {progress.get('current_step') or 'none'}")
        if payload.get("error"):
            lines.append(f"  error: {payload.get('error')}")
        lines.append("")
        lines.append("STEPS")
        for step in payload.get("steps") or []:
            line = f"  [{step.get('status', '?')}] {step.get('tool', '?')}"
            if step.get("error"):
                line += f" — {step.get('error')}"
            if step.get("attempts", 1) > 1:
                line += f" (attempts: {step.get('attempts')})"
            lines.append(line)
        if not (payload.get("steps") or []):
            lines.append("  no steps recorded")
        text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------ memory

    def _open_memory(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — Memory & Knowledge")
        win.resize(840, 580)
        lay = QVBoxLayout(win)
        head = QHBoxLayout()
        title = QLabel("MEMORY & KNOWLEDGE")
        title.setStyleSheet(f"color: {CYAN}; font-size: 14px; font-weight: 800;")
        head.addWidget(title, 1)
        self._mem_query = QLineEdit()
        self._mem_query.setPlaceholderText("Search knowledge…")
        self._mem_query.returnPressed.connect(self._memory_recall)
        head.addWidget(self._mem_query, 1)
        head.addWidget(self._chip("SEARCH", self._memory_recall, primary=True))
        head.addWidget(self._chip("REFRESH", self._memory_load))
        lay.addLayout(head)

        tabs = QTabWidget()
        self._mem_texts: dict[str, QTextEdit] = {}
        for tab_name in ("episodes", "knowledge", "search"):
            text = QTextEdit()
            text.setReadOnly(True)
            tabs.addTab(text, tab_name.upper())
            self._mem_texts[tab_name] = text
        lay.addWidget(tabs, 1)
        status = QLabel("")
        status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        lay.addWidget(status)
        self._mem_status = status

        self._mem_win = win
        self._memory_load()
        win.exec()

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
        if not getattr(self, "_mem_win", None):
            return
        episodes = data.get("episodes") or {}
        ep_items = episodes.get("episodes") or []
        ep_lines = [f"EPISODIC MEMORY ({episodes.get('count', len(ep_items))})"]
        if not ep_items:
            ep_lines.append("  no episodic memories yet")
        for ep in ep_items[:25]:
            ep_lines.append(f"  [{ep.get('id')}] {str(ep.get('command', ''))[:70]} — {ep.get('status', '?')}")
            if ep.get("summary"):
                ep_lines.append(f"      {str(ep.get('summary', ''))[:140]}")
        self._fill_memory_tab("episodes", ep_lines)

        knowledge = data.get("knowledge") or {}
        stats = knowledge.get("stats") or {}
        kn_lines = ["KNOWLEDGE BASE"]
        if stats:
            for key, value in stats.items():
                kn_lines.append(f"  {key}: {value}")
        else:
            kn_lines.append("  knowledge base empty — use 'remember' or 'index' commands")
        self._fill_memory_tab("knowledge", kn_lines)

    def _memory_recall(self) -> None:
        query = self._mem_query.text().strip()
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
        if not getattr(self, "_mem_win", None):
            return
        results = payload.get("results") or []
        lines = [f"SEARCH: {payload.get('query', '')}"]
        if not results:
            lines.append("  no matches")
        for hit in results[:20]:
            title = str(hit.get("title") or hit.get("doc_type") or "?")
            score = hit.get("score")
            content = str(hit.get("content") or "")[:160]
            score_str = f"  ({score:.2f})" if isinstance(score, (int, float)) else ""
            lines.append(f"  {title}{score_str}")
            if content:
                lines.append(f"      {content}")
        self._fill_memory_tab("search", lines)

    def _fill_memory_tab(self, key: str, lines: list[str]) -> None:
        text = self._mem_texts.get(key)
        if text is None:
            return
        text.clear()
        text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------ help / recap

    def _open_help(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — What can I do?")
        win.resize(740, 580)
        lay = QVBoxLayout(win)
        text = QTextEdit()
        text.setReadOnly(True)
        entries: list[str] = [
            "VOICE + DOCK",
            "  youtube pause          toggle play/pause",
            "  youtube next           next video",
            "  list apps              show apps in the launcher",
            "  open whatsapp chat <name>",
            "  open <app or site>     e.g. 'open chrome', 'open google'",
            "  search web <query>",
            "  take screenshot",
            "  play <song>            music via yt-dlp",
            "",
            "JOB AGENT",
            "  apply for <role> at <company>",
            "  job status / job cancel",
            "",
            f"TOOLS ({len(self.runtime.registry.catalog())})",
        ]
        for tool in self.runtime.registry.catalog():
            entries.append(f"  {tool.get('name', '')}")
            entries.append(f"      {tool.get('description', '')}  [risk: {tool.get('risk', 'low')}]")
        text.setPlainText("\n".join(entries))
        lay.addWidget(text)
        win.exec()

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
        win = QDialog(self)
        win.setWindowTitle("JARVIS — While you were away")
        win.resize(760, 600)
        lay = QVBoxLayout(win)
        text = QTextEdit()
        text.setReadOnly(True)

        lines: list[str] = ["WHILE YOU WERE AWAY", "", "COMMANDS"]
        counts = review.get("sources") or {}
        if counts:
            lines.append("  " + "  ·  ".join(f"{src}: {n}" for src, n in counts.items()))
        for row in (review.get("commands") or [])[-12:]:
            snippet = str(row.get("response_text") or "")[:120].replace("\n", " ")
            lines.append(f"  {row.get('created_at', '')[:16]} {row.get('source')}: {row.get('command')}")
            lines.append(f"      {snippet}")
        lines.append("")
        lines.append("TOOL ACTIVITY")
        tool_counts = review.get("tool_counts") or {}
        if tool_counts:
            lines.append("  " + "  ·  ".join(f"{k}: {v}" for k, v in tool_counts.items()))
        top_tools = review.get("top_tools") or {}
        if top_tools:
            lines.append("  Most used: " + ", ".join(top_tools))
        lines.append("")
        lines.append("JOB PIPELINE")
        job_status = review.get("job_status") or {}
        if job_status:
            lines.append("  " + "  ·  ".join(f"{k}: {v}" for k, v in job_status.items()))
        for row in (review.get("job_applications") or [])[-10:]:
            lines.append(f"  [{row.get('status')}] {row.get('title')} @ {row.get('company') or row.get('site')}")
        lines.append("")
        lines.append("SCHEDULED RUNS")
        runs = review.get("scheduled_runs") or []
        for run in runs[-10:]:
            lines.append(f"  [{run.get('status')}] {run.get('result', '')[:100]}")
        if not lines:
            lines = ["Nothing to report."]
        text.setPlainText("\n".join(lines))
        lay.addWidget(text)

        if auto:
            def _open_recap() -> None:
                win.close()
                self._raise_main_window()

            self._toaster.notify("While you were away", "Click to open the catch-up recap.", CYAN, on_click=_open_recap)
        win.exec()

    # ------------------------------------------------------------ autostart / tray

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
        self.auto_start_button.setText("AUTOSTART ON" if on else "AUTOSTART OFF")
        if on:
            self.auto_start_button.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
        else:
            self.auto_start_button.setStyleSheet(f"color: {AMBER}; background: {_rgba(DANGER, 220)};")

    def _toggle_tray(self) -> None:
        if not self._tray.available:
            self._toaster.notify("Tray unavailable", "pystray is not installed.", RED)
            return
        if self.isHidden():
            self._raise_main_window()
            return
        if not self._tray.start():
            self._raise_main_window()
            return
        self.tray_button.setText("TRAY ON")
        self.tray_button.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
        self._toaster.notify(
            "JARVIS in tray", "HUD is running in the tray.\nUse the JARVIS icon or Ctrl+Shift+J to bring it back.", GREEN
        )
        self.hide()

    def _tray_show(self) -> None:
        QTimer.singleShot(0, self._raise_main_window)

    def _tray_mini(self) -> None:
        QTimer.singleShot(0, self.mini.show)

    def _tray_exit(self) -> None:
        QTimer.singleShot(0, self._on_close)

    def _raise_main_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ persistence

    def _restore_ui_config(self) -> None:
        try:
            cfg = json.loads(self._ui_config_path.read_text(encoding="utf-8"))
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
            _apply_palette(self._theme)
            self.theme_button.setText(f"THEME {self._theme.upper()}")
            self.backdrop.set_bg(self._bg.get("mode", "aurora"), self._bg.get("value", ""))
            self.backdrop.set_gfx(self._gfx_full)
            self.setStyleSheet(_build_qss())
        except Exception:
            pass
        self.showMaximized()

    def _save_ui_config(self) -> None:
        try:
            cfg = {
                "mini_visible": self.mini.isVisible(),
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

    def _poll_summon_signal(self) -> None:
        try:
            path = Path(__file__).resolve().parent.parent / "data" / "summon.json"
            if path.exists():
                try:
                    cfg = json.loads(path.read_text(encoding="utf-8"))
                    stamp = float(cfg.get("ts", 0) or 0)
                    import time

                    if time.time() - stamp < 5:
                        self._raise_main_window()
                        self.mini.show()
                        self._toaster.notify("JARVIS", "Launch requested — window brought to front.", CYAN, duration_ms=2500)
                finally:
                    try:
                        path.unlink()
                    except OSError:
                        pass
        except Exception:
            pass

    # ------------------------------------------------------------ close

    def closeEvent(self, event) -> None:
        self._on_close()
        event.accept()

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

    def run(self) -> None:
        self.show()
        if os.environ.get("JARVIS_DUMP"):
            QTimer.singleShot(2500, self._dump_widgets)
        self._app.exec()

    def _dump_widgets(self) -> None:
        try:
            out = Path(os.environ.get("JARVIS_DUMP", "widgets.txt"))
            lines = []
            for w in self._app.allWidgets():
                g = w.geometry()
                cls = type(w).__name__
                obj = w.objectName()
                vis = "V" if w.isVisible() else "h"
                en = "E" if w.isEnabled() else "d"
                lines.append(f"{vis}{en} {cls} '{obj}' x={g.x()} y={g.y()} w={g.width()} h={g.height()}")
            out.write_text("\n".join(sorted(lines)), encoding="utf-8")
        except Exception as e:
            try:
                Path("widgets.txt").write_text(f"ERR {e}", encoding="utf-8")
            except Exception:
                pass


def _signal_running_instance() -> None:
    """Write a summon signal so an already-running HUD brings its window to front."""
    try:
        path = Path(__file__).resolve().parent.parent / "data" / "summon.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        import time

        path.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except Exception:
        pass


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
        _signal_running_instance()  # another HUD is running; ask it to come to the front
        return
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    window = JarvisHudApp(app)
    window.run()


if __name__ == "__main__":
    main()