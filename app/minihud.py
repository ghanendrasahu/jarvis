from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.monitor import ResourceMonitor
from app.core.palette import Palette

BG = "#020617"
PANEL = "#0a1428"
PANEL_2 = "#0d1a33"
STROKE = "#1e2c52"
CYAN = "#22d3ee"
TEXT = "#dbeafe"
MUTED = "#8ea6ca"
GREEN = "#86efac"
AMBER = "#fbbf24"
RED = "#fb7185"
GLASS = "rgba(10, 20, 40, 0.92)"
INDIGO = "#818cf8"
CHIP = "#0f1f3c"
CHIP_HI = "#183159"
DANGER = "#3a1a24"
ENTRY = "#040a16"
SEL = "#1c3257"

_PALETTE_KEYS = (
    "BG",
    "PANEL",
    "PANEL_2",
    "STROKE",
    "CYAN",
    "INDIGO",
    "TEXT",
    "MUTED",
    "GREEN",
    "AMBER",
    "RED",
    "CHIP",
    "CHIP_HI",
    "DANGER",
    "ENTRY",
    "SEL",
)


def _build_qss() -> str:
    return f"""
    QFrame#MiniShell {{
        background: {PANEL};
        border: 1px solid {STROKE};
        border-radius: 14px;
    }}
    QLabel#MiniBrand {{ color: {CYAN}; font-size: 13px; font-weight: 800; letter-spacing: 1px; }}
    QLabel#MiniCmd {{ color: {TEXT}; font-size: 12px; font-weight: 700; }}
    QLabel#MiniRes {{ color: {MUTED}; font-size: 11px; }}
    QLabel#MiniStat {{ color: {MUTED}; font-size: 10px; }}
    QPushButton#MiniBtn {{
        color: {MUTED};
        background: {_rgba(CHIP, 200)};
        border: 1px solid {_rgba(STROKE, 120)};
        border-radius: 8px;
        padding: 4px 10px;
        font-size: 10px;
        font-weight: 700;
    }}
    QPushButton#MiniBtn:hover {{ background: {_rgba(CHIP_HI, 230)}; }}
    QPushButton#MiniAccent {{
        color: {BG};
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {CYAN}, stop:1 {INDIGO});
        border: none;
        border-radius: 8px;
        padding: 4px 10px;
        font-size: 10px;
        font-weight: 800;
    }}
    QPushButton#MiniDanger {{ background: {_rgba(DANGER, 200)}; color: {RED}; border-radius: 8px; padding: 4px 10px; font-size: 10px; font-weight: 700; }}
    QLineEdit#MiniEntry {{
        background: {_rgba(ENTRY, 235)};
        border: 1px solid {_rgba(CYAN, 90)};
        border-radius: 8px;
        padding: 6px 8px;
        color: {TEXT};
        font-size: 11px;
        selection-background-color: {SEL};
    }}
    QLineEdit#MiniEntry:focus {{ border: 1px solid {CYAN}; background: {_rgba(SEL, 200)}; }}
    """


def _rgba(hex_color: str, alpha: int) -> str:
    c = hex_color.lstrip("#")
    if len(c) == 6:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"
    return f"rgba(0, 0, 0, {alpha})"


def apply_palette(palette: Palette) -> None:
    """Re-theme MiniHud's module-level colors from a palette dict (see app.core.palette)."""
    from typing import cast
    for key in palette:
        safe_key: str = cast(str, key)
        if safe_key in _PALETTE_KEYS:
            globals()[safe_key] = palette[safe_key]  # type: ignore[literal-required]


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


class VoiceMicButton(QWidget):
    """Custom-painted voice toggle: face with finger-on/lifted-from mouth."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__()
        self._on_click = on_click
        self._running = False
        self._pulse = 0.0
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Voice mode — start/stop listening")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(60)

    def _animate(self) -> None:
        if self._running:
            self._pulse += 0.08
        self.update()

    def update_status(self, running: bool) -> None:
        self._running = running
        if not running:
            self._pulse = 0.0

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        if self._running:
            glow = abs(math.sin(self._pulse)) * 0.35
            color = QColor(CYAN)
            color.setAlphaF(0.15 + glow)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(cx - 20), int(cy - 20), 40, 40)
            line_color = QColor(CYAN)
        else:
            line_color = QColor(MUTED)

        pen = QPen(line_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # head circle
        head_r = 13
        p.drawEllipse(int(cx - head_r), int(cy - head_r + 1), head_r * 2, head_r * 2)

        # eyes — two small dots
        p.setBrush(line_color)
        p.setPen(Qt.PenStyle.NoPen)
        ey = cy - 3
        p.drawEllipse(int(cx - 5) - 2, int(ey) - 2, 4, 4)
        p.drawEllipse(int(cx + 5) - 2, int(ey) - 2, 4, 4)

        # mouth — small horizontal line
        p.setPen(QPen(line_color, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        my = cy + 5
        p.drawLine(int(cx - 3), int(my), int(cx + 3), int(my))

        # finger — vertical line either on or above mouth
        p.setPen(QPen(line_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        fx = cx + 1
        if self._running:
            # finger lifted above mouth — voice is ON (unmuted)
            p.drawLine(int(fx), int(cy - 13), int(fx), int(cy - 8))
        else:
            # finger pressed on mouth — voice is OFF (muted)
            p.drawLine(int(fx), int(my - 2), int(fx), int(my + 8))

        p.end()


class MiniHud(QWidget):
    """Small frameless, always-on-top, draggable window showing live JARVIS activity."""

    def __init__(
        self,
        root: QWidget,
        runtime: Any,
        monitor: ResourceMonitor,
        on_run_command: Callable[[str], None],
        on_open_main: Callable[[], None],
    ) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.root = root
        self.runtime = runtime
        self.monitor = monitor
        self._on_run_command = on_run_command
        self._on_open_main = on_open_main
        self._config_path = Path(__file__).resolve().parent.parent / "data" / "mini_window.json"
        self._pinned = True
        self._drag_off: QPoint | None = None
        self._active_command = ""
        self._last_status = ""
        self._wave_phase = 0.0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)
        self.setStyleSheet(_build_qss())

        shell = QFrame(self)
        shell.setObjectName("MiniShell")
        shadow = QGraphicsDropShadowEffect(shell)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor("#000000")
        shell.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)

        lay = QVBoxLayout(shell)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #8ea6ca; font-size: 14px;")
        header.addWidget(self.dot)
        brand = QLabel("JARVIS")
        brand.setObjectName("MiniBrand")
        header.addWidget(brand, 1)

        self._pin_btn = self._mini_button("PIN", self._toggle_pin)
        header.addWidget(self._pin_btn)
        header.addWidget(self._mini_button("MAIN", self._on_open_main))
        header.addWidget(self._mini_button("✕", self.hide, danger=True))
        lay.addLayout(header)

        self.cmd_label = QLabel("ready")
        self.cmd_label.setObjectName("MiniCmd")
        self.cmd_label.setWordWrap(True)
        lay.addWidget(self.cmd_label)

        self.res_label = QLabel("")
        self.res_label.setObjectName("MiniRes")
        self.res_label.setWordWrap(True)
        lay.addWidget(self.res_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.mic_btn = VoiceMicButton(self._toggle_voice)
        input_row.addWidget(self.mic_btn)
        self.entry = QLineEdit()
        self.entry.setObjectName("MiniEntry")
        self.entry.setPlaceholderText("type a command…")
        self.entry.returnPressed.connect(self._run_quick)
        input_row.addWidget(self.entry, 1)
        run_btn = self._mini_button("RUN", self._run_quick, accent=True)
        input_row.addWidget(run_btn)
        lay.addLayout(input_row)

        bars = QHBoxLayout()
        bars.setSpacing(8)
        self.cpu_label = QLabel("CPU --%")
        self.cpu_label.setObjectName("MiniStat")
        bars.addWidget(self.cpu_label)
        self.cpu_bar = _MiniBar(90, 8, CYAN)
        bars.addWidget(self.cpu_bar, 1)
        self.ram_label = QLabel("RAM --%")
        self.ram_label.setObjectName("MiniStat")
        bars.addWidget(self.ram_label)
        self.ram_bar = _MiniBar(90, 8, AMBER)
        bars.addWidget(self.ram_bar, 1)
        lay.addLayout(bars)

        self._restore_position()
        self.hide()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(300)
        self._wave_timer = QTimer(self)
        self._wave_timer.timeout.connect(self._animate_dot)
        self._wave_timer.start(120)

    def _mini_button(self, text: str, callback, wide: bool = True, accent: bool = False, danger: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if not wide and not accent:
            btn.setFixedWidth(44)
        if accent:
            btn.setObjectName("MiniAccent")
        elif danger:
            btn.setObjectName("MiniDanger")
        else:
            btn.setObjectName("MiniBtn")
        btn.clicked.connect(callback)
        return btn

    def _drag_start(self, event) -> None:
        if isinstance(event.widget(), (QPushButton, QLineEdit)):
            self._drag_off = None
            return
        self._drag_off = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_off is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_off = None
        super().mouseReleaseEvent(event)

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        flags = self.windowFlags()
        if self._pinned:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self._pin_btn.setText("PIN" if self._pinned else "UNPIN")
        self.show()

    def _toggle_voice(self) -> None:
        def worker() -> None:
            try:
                if self.runtime.voice.is_running():
                    self.runtime.voice.stop()
                else:
                    self.runtime.voice.start()
            except Exception:
                pass
            QTimer.singleShot(0, lambda: self.mic_btn.update_status(
                self.runtime.voice.is_running()
            ))

        threading.Thread(target=worker, daemon=True).start()

    def _run_quick(self) -> None:
        text = self.entry.text().strip()
        if not text:
            return
        self.entry.clear()
        self._on_run_command(text)

    def notify_command(self, command: str) -> None:
        self._active_command = command
        self.cmd_label.setText(f"NOW: {command}")
        self.cmd_label.setStyleSheet("color: #22d3ee; font-weight: bold; font-size: 12px;")
        self.res_label.setText("working…")
        self.res_label.setStyleSheet("color: #fbbf24; font-size: 11px;")

    def apply_palette(self, palette: Palette) -> None:
        """Re-theme inline widgets after a theme change."""
        apply_palette(palette)
        self.setStyleSheet(_build_qss())
        self.cpu_bar.set_color(CYAN)
        self.ram_bar.set_color(AMBER)
        self._animate_dot()

    def show(self) -> None:
        super().show()
        self.raise_()
        self.activateWindow()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def close(self) -> bool:
        self._save_position()
        self.hide()
        return True

    def _animate_dot(self) -> None:
        self._wave_phase += 0.35
        try:
            status = self.runtime.voice.status()
        except Exception:
            status = {}
        _, color, _, _ = voice_phase(status)
        self.dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    def _tick(self) -> None:
        try:
            if not self.isVisible():
                return
            try:
                status = self.runtime.voice.status()
            except Exception:
                status = {}
            phase, color, _, _ = voice_phase(status)
            self.dot.setStyleSheet(f"color: {color}; font-size: 14px;")

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
                self.cmd_label.setText(f"NOW: {self._active_command}")
                self.cmd_label.setStyleSheet("color: #22d3ee; font-weight: bold; font-size: 12px;")
                self.res_label.setText("working…")
                self.res_label.setStyleSheet("color: #fbbf24; font-size: 11px;")
            elif last_command:
                self._last_status = last_status
                color_map = {
                    "completed": GREEN,
                    "ok": GREEN,
                    "pending": AMBER,
                    "failed": RED,
                }
                res_color = color_map.get(last_status, MUTED)
                self.cmd_label.setText(f"LAST: {last_command}")
                self.cmd_label.setStyleSheet("color: #dbeafe; font-size: 12px;")
                self.res_label.setText(last_status.upper())
                self.res_label.setStyleSheet(f"color: {res_color}; font-weight: bold; font-size: 11px;")
            else:
                self.cmd_label.setText("ready")
                self.cmd_label.setStyleSheet("color: #22d3ee; font-size: 12px;")
                self.res_label.setText("")
                self.res_label.setStyleSheet("color: #8ea6ca; font-size: 11px;")

            snap = None
            try:
                snap = self.monitor.snapshot()
            except Exception:
                snap = None
            if snap is not None:
                self.cpu_bar.set_value(float(snap.cpu_percent))
                self.cpu_label.setText(f"CPU {snap.cpu_percent:.0f}%")
                self.ram_bar.set_value(float(snap.memory_percent))
                self.ram_label.setText(f"RAM {snap.memory_percent:.0f}%")
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
        screen = self.screen()
        sw = screen.availableGeometry().width() if screen else 1920
        sh = screen.availableGeometry().height() if screen else 1080
        if x is None or y is None or x < 0 or y < 0 or x > sw or y > sh:
            x = sw - 390
            y = 90
        self.move(x, y)
        self._pin_btn.setText("PIN" if self._pinned else "UNPIN")

    def _save_position(self) -> None:
        try:
            cfg = {"x": int(self.x()), "y": int(self.y()), "pinned": self._pinned}
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(json.dumps(cfg), encoding="utf-8")
        except Exception:
            pass


class _MiniBar(QWidget):
    def __init__(self, width: int, height: int, color: str) -> None:
        super().__init__()
        self._value = 0.0
        self._color = color
        self.setFixedSize(width, height)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(PANEL_2), 1))
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
        fill_w = int((self.width() - 4) * (self._value / 100.0))
        if fill_w > 2:
            p.setBrush(QColor(self._color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(2, 2, fill_w, self.height() - 4, 3, 3)
        p.end()
