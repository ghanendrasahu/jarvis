from __future__ import annotations

import ctypes
import json
import math
import queue
import random
import re
import shutil
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.bootstrap import build_runtime
from app.core.chat_router import classify_user_input
from app.core.events import EventHub
from app.core.media import background_image_path, list_background_images
from app.core.palette import PALETTES, THEME_ORDER
from app.core.utils import build_startup_greeting
from app.hotkey import GlobalHotkey
from app.llm.chat import ChatService, build_history_from_memory
from app.minihud import MiniHud, voice_phase
from app.minihud import apply_palette as apply_mini_palette
from app.toast import Toaster
from app.tray import TrayIcon

BG = PANEL = PANEL_2 = STROKE = CYAN = INDIGO = TEXT = MUTED = GREEN = AMBER = RED = ""
INNER = CANVAS = TERM = LIST = ENTRY = CHIP = CHIP_HI = SEL = BTN2 = DANGER = OK = STATUS = SHELL = TOOLTIP = ""
TOOLTIP_BORDER = SCENE = RING2 = ""

_current_theme = "aurora"

FONT_MONO = "'Cascadia Mono', 'Consolas', monospace"

# Max agent-step widgets rendered per 80ms poll tick; storms collapse into one line.
_AGENT_STEP_BUDGET_PER_TICK = 30


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


def _title_color() -> str:
    return _blend(TEXT, MUTED, 0.42)


# --------------------------------------------------------------------- markdown

_MD_ESCAPE = ("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")
_FENCED_RE = re.compile(r"```[\w+-]*\n?(.*?)```", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_LIST_RE = re.compile(r"^(\s*)[-*]\s+(.+)$")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING_SIZES = {1: 16, 2: 15, 3: 14, 4: 13}


def _md_inline(text: str) -> str:
    text = _CODE_SPAN_RE.sub(
        rf'<span style="font-family:Consolas,monospace; background-color:{_rgba(CHIP, 220)}; color:{CYAN};">\1</span>',
        text,
    )
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _LINK_RE.sub(r'<a href="\2" style="color:' + CYAN + r';">\1</a>', text)
    return text


def markdown_to_html(raw: str) -> str:
    """Render a small, safe Markdown subset to Qt rich-text.

    Supports fenced code blocks, inline code, headings 1-4, bullet lists,
    blockquotes, **bold**, *italic*, and links. No external dependency.
    """
    if not raw or not raw.strip():
        return ""
    esc: str = raw
    for old, new in _MD_ESCAPE:
        esc = esc.replace(old, new)

    blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        code_lines = []
        for line in match.group(1).split("\n"):
            line = line.replace("\t", "&nbsp;" * 4)
            line = re.sub(r"  ", "&nbsp;&nbsp;", line)
            code_lines.append(line.replace(" ", "&nbsp;") or "")
        blocks.append(
            f'<table width="100%" cellspacing="0" cellpadding="7"><tr>'
            f'<td bgcolor="{TERM}"><span style="font-family:Consolas,monospace; '
            f'color:{TEXT}; font-size:11.5px;">{"<br>".join(code_lines)}</span></td></tr></table>'
        )
        return f"\x00{len(blocks) - 1}\x00"

    esc = _FENCED_RE.sub(_stash, esc)

    out_lines: list[str] = []
    for line in esc.split("\n"):
        heading = _HEADING_RE.match(line.strip())
        bullet = _LIST_RE.match(line.rstrip())
        if line.strip().startswith("&gt;"):
            out_lines.append(f'<i style="color:{MUTED};">{_md_inline(line.strip()[4:].strip())}</i>')
        elif heading:
            size = _HEADING_SIZES[len(heading.group(1))]
            out_lines.append(f'<b style="color:{TEXT}; font-size:{size}px;">{_md_inline(heading.group(2))}</b>')
        elif bullet:
            indent = "&nbsp;" * (len(bullet.group(1)) // 2)
            out_lines.append(f"{indent}&nbsp;&nbsp;•&nbsp;&nbsp;{_md_inline(bullet.group(2))}")
        else:
            out_lines.append(_md_inline(line))
    body = "<br>".join(out_lines)

    def _unstash(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return blocks[idx] if 0 <= idx < len(blocks) else ""

    return re.sub(r"\x00(\d+)\x00", _unstash, body).replace("<br><br><table", "<br><table")


def _build_qss() -> str:
    title = _title_color()
    return f"""
    * {{ font-family: 'Segoe UI'; outline: none; }}
    QMainWindow, QWidget {{ background: transparent; }}
    QDialog {{ background: {CANVAS}; }}

    QLabel {{ color: {TEXT}; font-size: 13px; background: transparent; }}
    QLabel#Brand {{ color: {TEXT}; font-size: 19px; font-weight: 800; letter-spacing: 5px; }}
    QLabel#BrandDot {{ color: {CYAN}; font-size: 15px; }}
    QLabel#BrandSub {{ color: {MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 2px; }}
    QLabel#CardTitle {{ color: {title}; font-size: 11px; font-weight: 700; letter-spacing: 1.6px; }}
    QLabel#CardSubtitle {{ color: {MUTED}; font-size: 10.5px; }}
    QLabel#SectionLabel {{ color: {title}; font-size: 10px; font-weight: 700; letter-spacing: 1.4px; }}
    QLabel#MetaLabel {{ color: {MUTED}; font-size: 11px; }}

    QFrame#Shell {{
        background: {_rgba(BG, 135)};
        border: 1px solid {_rgba(STROKE, 145)};
        border-radius: 16px;
    }}
    QFrame#Card {{
        background: {_rgba(PANEL_2, 206)};
        border: 1px solid {_rgba(STROKE, 150)};
        border-radius: 14px;
    }}
    QFrame#AccentBar {{ background: {CYAN}; border: none; }}
    QFrame#HSep {{ background: {_rgba(STROKE, 125)}; max-height: 1px; border: none; }}

    QPushButton {{
        color: {TEXT};
        background: {_rgba(CHIP, 205)};
        border: 1px solid {_rgba(STROKE, 130)};
        border-radius: 8px;
        padding: 7px 14px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {_rgba(CHIP_HI, 235)}; border-color: {_rgba(STROKE, 195)}; }}
    QPushButton:pressed {{ background: {SEL}; }}
    QPushButton:disabled {{ color: {_rgba(MUTED, 130)}; background: {_rgba(CHIP, 105)}; border-color: {_rgba(STROKE, 65)}; }}
    QPushButton:focus {{ border-color: {_rgba(CYAN, 170)}; }}
    QPushButton[kind="primary"] {{
        color: {BG};
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {CYAN}, stop:1 {INDIGO});
        border: none;
        font-weight: 800;
        padding: 9px 18px;
    }}
    QPushButton[kind="primary"]:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_blend(CYAN, '#ffffff', 0.16)}, stop:1 {_blend(INDIGO, '#ffffff', 0.16)});
    }}
    QPushButton[kind="primary"]:pressed {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_blend(CYAN, '#000000', 0.18)}, stop:1 {_blend(INDIGO, '#000000', 0.18)});
    }}
    QPushButton[kind="primary"]:disabled {{
        color: {_rgba(BG, 140)};
        background: {_rgba(CHIP_HI, 120)};
    }}
    QPushButton[kind="tertiary"] {{
        color: {MUTED};
        background: transparent;
        border: 1px solid {_rgba(STROKE, 95)};
    }}
    QPushButton[kind="tertiary"]:hover {{ color: {TEXT}; background: {_rgba(CHIP, 170)}; }}
    QPushButton[size="small"] {{ padding: 4px 11px; font-size: 11px; border-radius: 6px; }}
    QPushButton[size="icon"] {{ padding: 0; min-width: 34px; max-width: 34px; min-height: 33px; font-size: 13px; }}

    QLineEdit, QComboBox {{
        background: {_rgba(ENTRY, 235)};
        border: 1px solid {_rgba(CYAN, 75)};
        border-radius: 10px;
        padding: 9px 12px;
        color: {TEXT};
        font-size: 13px;
        selection-background-color: {SEL};
    }}
    QLineEdit:hover {{ border-color: {_rgba(CYAN, 150)}; }}
    QLineEdit:focus {{ border: 1px solid {CYAN}; background: {_rgba(SEL, 185)}; }}
    QLineEdit::placeholder {{ color: {_rgba(MUTED, 205)}; }}
    QLineEdit[mono="true"] {{ font-family: {FONT_MONO}; font-size: 12.5px; }}
    QComboBox::drop-down {{ width: 26px; border: none; }}
    QComboBox::down-arrow {{
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {MUTED};
        margin-right: 9px;
    }}
    QComboBox QAbstractItemView {{
        background: {PANEL_2}; color: {TEXT};
        border: 1px solid {STROKE};
        selection-background-color: {SEL};
        outline: none;
    }}

    QTextEdit {{
        background: {_rgba(LIST, 205)};
        border: 1px solid {_rgba(STROKE, 115)};
        border-radius: 10px;
        color: {TEXT};
        padding: 8px;
        font-size: 12.5px;
        selection-background-color: {SEL};
    }}
    QTextEdit#ChatFlow, QTextEdit#FeedView {{ background: transparent; border: none; padding: 0; }}
    QTextEdit#TermView {{
        background: {_rgba(TERM, 228)};
        border: 1px solid {_rgba(STROKE, 118)};
        border-radius: 10px;
        font-family: {FONT_MONO};
        font-size: 11px;
        padding: 8px 10px;
    }}

    QListWidget {{
        background: {_rgba(LIST, 202)};
        border: 1px solid {_rgba(STROKE, 112)};
        border-radius: 10px;
        color: {TEXT};
        font-size: 12.5px;
        padding: 4px;
        outline: none;
    }}
    QListWidget::item {{ padding: 6px 9px; border-radius: 6px; margin: 1px 2px; }}
    QListWidget::item:hover {{ background: {_rgba(CHIP, 150)}; }}
    QListWidget::item:selected {{ background: {SEL}; color: {TEXT}; }}

    QProgressBar {{
        background: {_rgba(TERM, 200)};
        border: 1px solid {_rgba(STROKE, 120)};
        border-radius: 5px;
        height: 10px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {CYAN}, stop:1 {INDIGO});
        border-radius: 4px;
    }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {_rgba(CHIP_HI, 175)}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {_rgba(CHIP_HI, 230)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {_rgba(CHIP_HI, 175)}; border-radius: 4px; min-width: 28px; }}
    QScrollBar::handle:horizontal:hover {{ background: {_rgba(CHIP_HI, 230)}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

    QMenu {{
        background: {PANEL_2}; color: {TEXT};
        border: 1px solid {_rgba(STROKE, 200)};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 7px 22px; border-radius: 6px; font-size: 12px; }}
    QMenu::item:selected {{ background: {SEL}; }}
    QMenu::item:disabled {{ color: {MUTED}; }}
    QMenu::separator {{ height: 1px; background: {_rgba(STROKE, 150)}; margin: 5px 8px; }}

    QTabWidget::pane {{ border: none; top: 6px; }}
    QTabBar::tab {{
        background: {_rgba(CHIP, 180)}; color: {MUTED};
        padding: 7px 16px; border-radius: 8px;
        margin-right: 4px; font-size: 11.5px; font-weight: 700;
    }}
    QTabBar::tab:selected {{ background: {SEL}; color: {TEXT}; }}

    QSplitter::handle {{ background: {_rgba(STROKE, 60)}; }}
    QSplitter::handle:hover {{ background: {_rgba(CYAN, 130)}; }}
    QSplitter::handle:horizontal {{ width: 3px; }}

    QToolTip {{
        background: {TOOLTIP}; color: {TEXT};
        border: 1px solid {TOOLTIP_BORDER};
        padding: 6px 9px; border-radius: 6px; font-size: 12px;
    }}

    QGroupBox {{
        color: {title};
        border: 1px solid {_rgba(STROKE, 130)};
        border-radius: 10px;
        margin-top: 15px;
        padding: 12px 10px 10px 10px;
        font-size: 11px; font-weight: 700;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 10px; top: 5px;
        padding: 0 5px; background: {CANVAS}; letter-spacing: 1.2px;
    }}

    QRadioButton {{ color: {TEXT}; font-size: 12.5px; spacing: 7px; }}
    QRadioButton::indicator {{
        width: 15px; height: 15px; border-radius: 8px;
        border: 1px solid {_rgba(STROKE, 220)};
        background: {_rgba(ENTRY, 200)};
    }}
    QRadioButton::indicator:checked {{
        border: 1px solid {CYAN};
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.45, fx:0.5, fy:0.5, stop:0 {CYAN}, stop:0.55 {CYAN}, stop:0.8 rgba(0,0,0,0), stop:1 rgba(0,0,0,0));
    }}

    QSlider::groove:horizontal {{ height: 4px; background: {_rgba(CHIP_HI, 200)}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {_rgba(CYAN, 170)}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 15px; height: 15px; margin: -6px 0;
        border-radius: 8px; background: {CYAN}; border: 2px solid {BG};
    }}

    QFrame#Toast {{
        background: {PANEL_2};
        border: 1px solid {_rgba(STROKE, 190)};
        border-radius: 12px;
    }}
    QLabel#ToastTitle {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
    QLabel#ToastMsg {{ color: {MUTED}; font-size: 11.5px; }}
    QLabel#ToastClose {{ color: {MUTED}; font-size: 14px; }}
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
        self._timer.start(50)

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
        self._angle += 0.02
        if not self._particles and self.width() > 10:
            w, h = self.width(), self.height()
            count = 44 if self._gfx_full else 12
            for _ in range(count):
                self._particles.append(
                    {
                        "x": random.uniform(0, w),
                        "y": random.uniform(0, h),
                        "r": random.uniform(1.0, 2.6),
                        "speed": random.uniform(0.15, 0.7),
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
            p.fillRect(0, 0, w, h, QColor(4, 8, 20, 178))
        elif self._mode == "gradient":
            idx = int(self._value or 0) % len(self.GRADIENTS)
            _name, top, bottom = self.GRADIENTS[idx]
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor(top))
            grad.setColorAt(1, QColor(bottom))
            p.fillRect(0, 0, w, h, grad)
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor("#04091a"))
            grad.setColorAt(1, QColor("#0c1830"))
            p.fillRect(0, 0, w, h, grad)
            self._draw_blob(p, w * 0.18, h * 0.30, 340 + 26 * math.sin(self._angle), INDIGO, 20)
            self._draw_blob(p, w * 0.82, h * 0.72, 400 + 34 * math.cos(self._angle * 0.7), CYAN, 13)

        for part in self._particles:
            part["y"] += part["speed"]
            part["x"] += math.sin(self._angle + part["y"] * 0.02) * 0.3
            if part["y"] > h + 8:
                part["y"] = -8
                part["x"] = random.uniform(0, w)
            r = part["r"]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(129, 140, 248, 85))
            p.drawEllipse(QPointF(part["x"], part["y"]), r, r)

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


class CoreOrb(QWidget):
    """Subtle rotating-ring core visualization for the assistant idle state."""

    def __init__(self) -> None:
        super().__init__()
        self._t = 0.0
        self.setFixedSize(180, 180)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self) -> None:
        if not self.isVisible():
            return
        self._t += 0.02
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2

        pen = QPen(QColor(CYAN), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        c = QColor(CYAN)
        c.setAlpha(115)
        pen.setColor(c)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - 74, cy - 74, 148, 148), int(-self._t * 57.3 * 16) % 5760, 100 * 16)

        pen2 = QPen(QColor(INDIGO), 2)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        c2 = QColor(INDIGO)
        c2.setAlpha(80)
        pen2.setColor(c2)
        p.setPen(pen2)
        p.drawArc(QRectF(cx - 56, cy - 56, 112, 112), int(self._t * 92 * 16) % 5760, 62 * 16)

        pen3 = QPen(QColor(CYAN), 1)
        c3 = QColor(CYAN)
        c3.setAlpha(45)
        pen3.setColor(c3)
        p.setPen(pen3)
        p.drawArc(QRectF(cx - 38, cy - 38, 76, 76), int(-self._t * 41 * 16) % 5760, 140 * 16)

        pulse = abs(math.sin(self._t * 1.6))
        halo_r = 22 + pulse * 5
        grad = QLinearGradient(cx - halo_r, cy, cx + halo_r, cy)
        hc = QColor(CYAN)
        hc.setAlpha(int(42 + pulse * 28))
        grad.setColorAt(0, hc)
        hf = QColor(CYAN)
        hf.setAlpha(0)
        grad.setColorAt(1, hf)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        core = QColor(CYAN)
        core.setAlpha(210)
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), 6 + pulse * 1.6, 6 + pulse * 1.6)

        p.end()


class IdlePanel(QWidget):
    """Premium empty state shown until the first conversation message arrives."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(6)
        lay.addStretch(3)
        self.orb = CoreOrb()
        lay.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(10)
        title = QLabel("JARVIS")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet(f"color: {TEXT}; font-size: 27px; font-weight: 800; letter-spacing: 11px;")
        lay.addWidget(title)
        state = QLabel("READY")
        state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        state.setStyleSheet(f"color: {GREEN}; font-size: 12px; font-weight: 700; letter-spacing: 4px;")
        lay.addWidget(state)
        cap = QLabel("System operational — awaiting your command")
        cap.setObjectName("MetaLabel")
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(cap)
        lay.addStretch(2)
        hint = QLabel("Try  ·  system status  ·  open chrome  ·  apply for data scientist  ·  what can you do?")
        hint.setObjectName("MetaLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(hint)
        lay.addStretch(1)


class WaveWidget(QWidget):
    """Clickable voice waveform visualizer."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__()
        self._on_click = on_click
        self._phase = 0.0
        self._color = MUTED
        self._amp = 6.0
        self._speed = 0.18
        self.setMinimumHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(50)

    def _step(self) -> None:
        self._phase += self._speed
        if self.isVisible():
            self.update()

    def update_status(self, color: str, amp: float, speed: float) -> None:
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

        base = QColor(self._color)
        base.setAlpha(36)
        p.setPen(QPen(base, 1))
        p.drawLine(0, c, w, c)

        echo = QColor(self._color)
        echo.setAlpha(70)
        p.setPen(QPen(echo, 1))
        path_e = QPainterPath()
        first = True
        for x in range(0, w + 1, 6):
            y = c + math.sin((x * 0.045) + self._phase * 0.82 + 1.4) * self._amp * 0.55
            if first:
                path_e.moveTo(x, y)
                first = False
            else:
                path_e.lineTo(x, y)
        p.drawPath(path_e)

        pen = QPen(QColor(self._color), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
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
        p.end()


class MeterBar(QWidget):
    """Tiny inline telemetry bar."""

    def __init__(self, color: str, width: int = 84, height: int = 6) -> None:
        super().__init__()
        self._value = 0.0
        self._color = color
        self.setFixedSize(width, height)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TERM))
        p.drawRoundedRect(self.rect(), 3, 3)
        fill_w = int((self.width() - 2) * (self._value / 100.0))
        if fill_w > 2:
            c = QColor(self._color)
            if self._value >= 90:
                c = QColor(RED)
            elif self._value >= 75:
                c = QColor(AMBER)
            p.setBrush(c)
            p.drawRoundedRect(1, 1, fill_w, self.height() - 2, 2, 2)
        p.end()


class JarvisHudApp(QMainWindow):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._hub = EventHub()
        self.runtime = build_runtime(events=self._hub)
        self._monitor = self.runtime.monitor
        self.setWindowTitle("JARVIS — AI Command Console")
        self.setMinimumSize(1120, 700)

        self._ui_config_path = Path(__file__).resolve().parent.parent / "data" / "jarvis_ui.json"
        self.mini = MiniHud(
            self, self.runtime, self._monitor, on_run_command=self._run_mini_command, on_open_main=self._raise_main_window
        )
        self.mini.hide()

        self._events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=2000)
        self._busy = False
        self._busy_seq = 0
        self._done_seqs: set[int] = set()
        self._pending_ids: list[str] = []
        self._chat_streaming = False
        self._chat_responding = False
        self._ai_state_key = "idle"
        self._chat_service = ChatService(self.runtime.settings)
        self._chat_cancel: threading.Event | None = None
        self._chat_stream_start_offset = -1
        self._chat_stream_text: list[str] = []
        self._last_user_text = ""
        self._pending_images: list[str] = []
        self._cmd_history: list[str] = []
        self._cmd_history_index = -1
        self._scheduler_announce: Callable[[dict[str, Any], str, str], None] | None = None
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
        if self._hotkey.start(lambda: self._enqueue(("summon_mini", None))):
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
        self._startup_greeting()

        self._restore_ui_config()
        self._scheduler_announce = self.runtime.scheduler.on_run
        self.runtime.scheduler.on_run = self._scheduler_on_run
        self.runtime.scheduler.start()
        self._job_tick()

    def _startup_greeting(self) -> None:
        settings = self.runtime.settings
        if not settings.startup_greeting_enabled:
            return
        greeting = build_startup_greeting()
        self._append_terminal("JARVIS", greeting)
        if not settings.tts_enabled:
            return

        def _speak() -> None:
            try:
                self.runtime.voice.speaker.speak(greeting)
            except Exception:
                from app.core.logging import get_logger

                get_logger("gui").warning("startup greeting speech failed", exc_info=True)

        threading.Thread(target=_speak, daemon=True, name="jarvis-startup-greeting").start()

    # ------------------------------------------------------------ shared builders

    def _make_card(self, title: str, subtitle: str = "", extra: QWidget | None = None) -> tuple[QFrame, QWidget]:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        bar = QFrame()
        bar.setObjectName("AccentBar")
        bar.setFixedSize(3, 13)
        head.addWidget(bar)
        title_label = QLabel(title.upper())
        title_label.setObjectName("CardTitle")
        head.addWidget(title_label)
        head.addStretch(1)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("CardSubtitle")
            head.addWidget(sub)
        if extra is not None:
            head.addWidget(extra)
        lay.addLayout(head)
        body = QWidget()
        lay.addWidget(body, 1)
        return card, body

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionLabel")
        return lbl

    def _button(
        self,
        text: str,
        callback: Callable[..., None],
        kind: str = "secondary",
        small: bool = False,
        tooltip: str = "",
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("kind", kind)
        if small:
            btn.setProperty("size", "small")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(lambda: callback())
        return btn

    def _chip(self, text: str, command, primary: bool = False) -> QPushButton:
        return self._button(text, command, kind="primary" if primary else "secondary")

    def _vsep(self) -> QFrame:
        f = QFrame()
        f.setFixedSize(1, 18)
        f.setStyleSheet(f"background: {_rgba(STROKE, 165)}; border: none;")
        return f

    def _hsep(self) -> QFrame:
        f = QFrame()
        f.setObjectName("HSep")
        f.setFixedHeight(1)
        return f

    # ------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        self.backdrop = Backdrop()
        self.backdrop.set_bg(self._bg.get("mode", "aurora"), self._bg.get("value", ""))
        self.backdrop.set_gfx(self._gfx_full)
        self.setCentralWidget(self.backdrop)
        self.setStyleSheet(_build_qss())

        root_lay = QVBoxLayout(self.backdrop)
        root_lay.setContentsMargins(16, 12, 16, 12)
        root_lay.setSpacing(10)

        root_lay.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)

        left = self._build_left_column()
        left.setMinimumWidth(320)
        center = self._build_center_column()
        center.setMinimumWidth(430)
        right_scroll = self._build_right_column()
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([430, 660, 370])

        root_lay.addWidget(splitter, 1)
        root_lay.addWidget(self._build_status_bar())

        self._refresh_status_meters()

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("Shell")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(18, 10, 14, 10)
        header_lay.setSpacing(8)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        dot = QLabel("●")
        dot.setObjectName("BrandDot")
        brand_row.addWidget(dot)
        brand = QLabel("JARVIS")
        brand.setObjectName("Brand")
        brand_row.addWidget(brand)
        brand_box.addLayout(brand_row)
        sub = QLabel("AI COMMAND CONSOLE")
        sub.setObjectName("BrandSub")
        brand_box.addWidget(sub)
        header_lay.addLayout(brand_box)
        header_lay.addSpacing(10)

        self.online_label = QLabel("● ONLINE")
        self.online_label.setStyleSheet(
            f"color: {GREEN}; font-size: 11px; font-weight: 700; letter-spacing: 1px;"
            f" padding: 4px 12px; background: {_rgba(GREEN, 26)}; border-radius: 9px;"
        )
        header_lay.addWidget(self.online_label)

        header_lay.addStretch(1)

        play_btn = self._button("▶", lambda: self._run_preset("youtube pause"), tooltip="Toggle YouTube play/pause")
        play_btn.setProperty("size", "icon")
        header_lay.addWidget(play_btn)
        next_btn = self._button("⏭", lambda: self._run_preset("youtube next"), tooltip="Go to next YouTube video")
        next_btn.setProperty("size", "icon")
        header_lay.addWidget(next_btn)
        header_lay.addWidget(self._button("APPS", lambda: self._run_preset("list apps"), tooltip="Show apps in the launcher panel"))

        header_lay.addWidget(self._vsep())
        header_lay.addWidget(self._button("MINI", self.mini.toggle, tooltip="Show/hide the floating mini HUD"))
        header_lay.addWidget(self._button("DIAGNOSE", self._diagnose_system, tooltip="Run full system diagnosis (CPU/RAM/GPU + process analysis)"))

        header_lay.addWidget(self._vsep())
        menu_btn = self._button("MENU ▾", self._show_more_menu, kind="tertiary")
        header_lay.addWidget(menu_btn)
        self._more_menu = QMenu(self)
        self._more_menu.addAction("HELP  (F1)", self._open_help)
        self._more_menu.addAction("SCHEDULER", self._open_scheduler)
        self._more_menu.addAction("HISTORY", self._open_history)
        self._more_menu.addAction("TASKS", self._open_tasks)
        self._more_menu.addAction("MEMORY", self._open_memory)
        self._more_menu.addAction("BACKGROUNDS", self._open_backgrounds)
        self._more_menu.addSeparator()
        self._more_menu.addAction("VOICE SETTINGS…", self._open_voice_settings)
        self.act_autostart = self._more_menu.addAction("AUTOSTART · ON", self._toggle_auto_start)
        self.act_tray = self._more_menu.addAction("TRAY · OFF", self._toggle_tray)
        self.act_gfx = self._more_menu.addAction("GRAPHICS · LITE", self._toggle_gfx)
        self.act_theme = self._more_menu.addAction(f"THEME · {self._theme.upper()}", self._cycle_theme)
        self._more_btn = menu_btn

        return header

    def _build_left_column(self) -> QWidget:
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        cmd_card, cmd_body = self._make_card("Command Center", "Natural-language control")
        cmd_lay = QVBoxLayout(cmd_body)
        cmd_lay.setContentsMargins(0, 0, 0, 0)
        cmd_lay.setSpacing(9)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.command_entry = QLineEdit()
        self.command_entry.setProperty("mono", True)
        self.command_entry.setPlaceholderText("Type a command… e.g. “system status”")
        self.command_entry.returnPressed.connect(self._run_command)
        self.command_entry.installEventFilter(self)
        row.addWidget(self.command_entry, 1)
        self.run_button = self._chip("EXECUTE", self._run_command, primary=True)
        row.addWidget(self.run_button)
        cmd_lay.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for label, cmd_str in [
            ("SYSTEM STATUS", "system status"),
            ("METRICS", "metrics"),
            ("SCHEDULES", "list schedules"),
            ("JOB STATUS", "job status"),
            ("TIME", "get current time"),
        ]:
            chips.addWidget(self._button(label, lambda c=cmd_str: self._run_preset(c), kind="tertiary", small=True))
        chips.addStretch(1)
        cmd_lay.addLayout(chips)
        left_lay.addWidget(cmd_card)

        feed_card, feed_body = self._make_card("Activity Feed", "Commands · agents · scheduler")
        feed_lay = QVBoxLayout(feed_body)
        feed_lay.setContentsMargins(0, 0, 0, 0)
        feed_lay.setSpacing(6)
        feed_head = QHBoxLayout()
        feed_head.addStretch(1)
        feed_head.addWidget(self._button("CLEAR", self._clear_feed, kind="tertiary", small=True))
        feed_lay.addLayout(feed_head)
        self.feed_text = QTextEdit()
        self.feed_text.setObjectName("FeedView")
        self.feed_text.setReadOnly(True)
        self.feed_text.setMinimumHeight(110)
        feed_lay.addWidget(self.feed_text, 1)
        left_lay.addWidget(feed_card, 2)

        term_card, term_body = self._make_card("Live Terminal", "Console output")
        term_lay = QVBoxLayout(term_body)
        term_lay.setContentsMargins(0, 0, 0, 0)
        term_lay.setSpacing(6)
        term_head = QHBoxLayout()
        term_hint = QLabel("Ctrl+L clears")
        term_hint.setObjectName("MetaLabel")
        term_head.addWidget(term_hint, 1)
        term_head.addWidget(self._button("CLEAR", self._clear_terminal, kind="tertiary", small=True))
        term_lay.addLayout(term_head)
        self.terminal = QTextEdit()
        self.terminal.setObjectName("TermView")
        self.terminal.setReadOnly(True)
        self.terminal.setMinimumHeight(160)
        term_lay.addWidget(self.terminal, 1)
        left_lay.addWidget(term_card, 3)

        return left

    def _build_center_column(self) -> QFrame:
        self.ai_state_chip = QLabel("● READY")
        self.ai_state_chip.setStyleSheet(self._ai_chip_qss(GREEN))

        ai_card, ai_body = self._make_card("AI Assistant", "Plans · executes · answers", extra=self.ai_state_chip)
        ai_lay = QVBoxLayout(ai_body)
        ai_lay.setContentsMargins(0, 0, 0, 0)
        ai_lay.setSpacing(10)

        self.ai_stack = QStackedWidget()
        self.idle_panel = IdlePanel()
        self.chat_text = QTextEdit()
        self.chat_text.setObjectName("ChatFlow")
        self.chat_text.setReadOnly(True)
        self.ai_stack.addWidget(self.idle_panel)
        self.ai_stack.addWidget(self.chat_text)
        ai_lay.addWidget(self.ai_stack, 1)

        composer = QFrame()
        composer_lay = QVBoxLayout(composer)
        composer_lay.setContentsMargins(0, 0, 0, 0)
        composer_lay.setSpacing(6)
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.chat_image_button = self._button(
            "IMAGE", self._attach_chat_images, kind="tertiary", small=True, tooltip="Attach an image for a vision-capable chat model"
        )
        input_row.addWidget(self.chat_image_button)
        self.chat_entry = QLineEdit()
        self.chat_entry.setMinimumHeight(40)
        self.chat_entry.setPlaceholderText("Ask JARVIS anything…  (commands like “open chrome” run directly)")
        self.chat_entry.returnPressed.connect(self._on_ask_clicked)
        input_row.addWidget(self.chat_entry, 1)
        self.ask_button = self._chip("ASK", self._on_ask_clicked, primary=True)
        self.ask_button.setMinimumHeight(40)
        self.ask_button.setToolTip("Send · while streaming this becomes STOP")
        input_row.addWidget(self.ask_button)
        composer_lay.addLayout(input_row)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        primary_spec = self._chat_service.chain[0] if self._chat_service.chain else None
        self.chat_status_label = QLabel(
            f"{primary_spec.name} · {primary_spec.model}" if primary_spec else "no chat provider configured"
        )
        self.chat_status_label.setObjectName("MetaLabel")
        meta_row.addWidget(self.chat_status_label, 1)
        meta_row.addWidget(self._button("RETRY", self._retry_last_chat, kind="tertiary", small=True, tooltip="Re-send the last message"))
        meta_row.addWidget(self._button("CLEAR", self._clear_conversation, kind="tertiary", small=True, tooltip="Clear the conversation view (Ctrl+Shift+C)"))
        composer_lay.addLayout(meta_row)
        ai_lay.addWidget(composer)

        return ai_card

    @staticmethod
    def _ai_chip_qss(color: str) -> str:
        return (
            f"color: {color}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px;"
            f" padding: 3px 11px; background: {_rgba(color, 28)}; border-radius: 9px;"
        )

    def _set_ai_state(self, key: str) -> None:
        states = {
            "idle": ("READY", GREEN),
            "thinking": ("THINKING", INDIGO),
            "processing": ("PROCESSING", CYAN),
            "responding": ("RESPONDING", GREEN),
            "error": ("ERROR", RED),
        }
        self._ai_state_key = key
        label, color = states.get(key, states["idle"])
        self.ai_state_chip.setText(f"● {label}")
        self.ai_state_chip.setStyleSheet(self._ai_chip_qss(color))

    def _flash_ai_error(self) -> None:
        self._set_ai_state("error")
        QTimer.singleShot(3500, self._clear_ai_error)

    def _clear_ai_error(self) -> None:
        if self._ai_state_key == "error":
            self._set_ai_state("idle")

    def _ensure_chat_visible(self) -> None:
        if self.ai_stack.currentIndex() != 1:
            self.ai_stack.setCurrentIndex(1)

    def _build_right_column(self) -> QScrollArea:
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(330)

        right = QWidget()
        right.setMinimumWidth(310)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(12)

        # ---- Voice engine
        self.voice_dot = QLabel("●")
        self.voice_dot.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        voice_card, voice_body = self._make_card("Voice Engine", "Wake word + speech", extra=self.voice_dot)
        voice_lay = QVBoxLayout(voice_body)
        voice_lay.setContentsMargins(0, 0, 0, 0)
        voice_lay.setSpacing(7)
        self.voice_state_label = QLabel("OFFLINE")
        self.voice_state_label.setStyleSheet(f"color: {MUTED}; font-size: 17px; font-weight: 800; letter-spacing: 2px;")
        voice_lay.addWidget(self.voice_state_label)
        self.voice_status_label = QLabel("Voice engine idle")
        self.voice_status_label.setObjectName("MetaLabel")
        voice_lay.addWidget(self.voice_status_label)
        self.voice_wave = WaveWidget(self._toggle_voice)
        voice_lay.addWidget(self.voice_wave)
        self.voice_switch_button = self._button("ENABLE MICROPHONE", self._toggle_voice, kind="primary")
        self.voice_switch_button.setMinimumHeight(38)
        voice_lay.addWidget(self.voice_switch_button)
        voice_lay.addWidget(self._button("VOICE SETTINGS…", self._open_voice_settings, kind="tertiary", small=True))
        right_lay.addWidget(voice_card)

        # ---- Job agent
        job_card, job_body = self._make_card("Job Agent", "Autonomous hunter")
        job_lay = QVBoxLayout(job_body)
        job_lay.setContentsMargins(0, 0, 0, 0)
        job_lay.setSpacing(8)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.job_state_label = QLabel("IDLE")
        self.job_state_label.setStyleSheet(
            f"color: {MUTED}; font-size: 11px; font-weight: 800; letter-spacing: 1px;"
            f" padding: 3px 11px; background: {_rgba(MUTED, 24)}; border-radius: 9px;"
        )
        status_row.addWidget(self.job_state_label)
        status_row.addStretch(1)
        self.job_phase_label = QLabel("-")
        self.job_phase_label.setObjectName("MetaLabel")
        status_row.addWidget(self.job_phase_label)
        job_lay.addLayout(status_row)
        self.job_reason_label = QLabel("")
        self.job_reason_label.setStyleSheet(f"color: {AMBER}; font-size: 11px;")
        self.job_reason_label.setWordWrap(True)
        self.job_reason_label.hide()
        job_lay.addWidget(self.job_reason_label)
        self.job_progress = QProgressBar()
        self.job_progress.setMaximum(100)
        self.job_progress.setValue(0)
        self.job_progress.setTextVisible(False)
        job_lay.addWidget(self.job_progress)

        job_stats = QGridLayout()
        job_stats.setSpacing(6)
        stat_colors = {
            "APPLIED": GREEN, "SKIPPED": AMBER, "FAILED": RED,
            "INTERVIEW": CYAN, "OFFER": GREEN, "REJECTED": RED,
        }
        self.job_stat_labels: dict[str, QLabel] = {}
        for i, name in enumerate(("APPLIED", "SKIPPED", "FAILED", "INTERVIEW", "OFFER", "REJECTED")):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            val = QLabel("0")
            val.setStyleSheet(f"color: {stat_colors[name]}; font-size: 16px; font-weight: 800;")
            cap = QLabel(name)
            cap.setStyleSheet(f"color: {MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 1px;")
            cell.addWidget(val)
            cell.addWidget(cap)
            wrap = QWidget()
            wrap.setLayout(cell)
            job_stats.addWidget(wrap, i // 3, i % 3)
            self.job_stat_labels[name] = val
        job_lay.addLayout(job_stats)

        job_lay.addWidget(self._hsep())

        qrow = QHBoxLayout()
        qrow.setSpacing(8)
        self.job_query_entry = QLineEdit()
        self.job_query_entry.setPlaceholderText("Role query — e.g. python developer")
        self.job_query_entry.returnPressed.connect(self._start_job_apply)
        qrow.addWidget(self.job_query_entry, 1)
        qrow.addWidget(self._chip("APPLY", self._start_job_apply, primary=True))
        job_lay.addLayout(qrow)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        ctrl.addWidget(self._button("STATUS", self._refresh_job_status, kind="tertiary", small=True))
        ctrl.addWidget(self._button("CANCEL", self._cancel_job, kind="tertiary", small=True))
        ctrl.addWidget(self._button("RESUME", self._resume_job, kind="tertiary", small=True))
        ctrl.addStretch(1)
        job_lay.addLayout(ctrl)

        job_lay.addWidget(self._section_label("Recent applications"))
        self.recent_list = QListWidget()
        self.recent_list.setMinimumHeight(96)
        job_lay.addWidget(self.recent_list, 1)
        right_lay.addWidget(job_card)

        # ---- Approvals
        self._approval_badge = QLabel("0")
        self._approval_badge.setStyleSheet(
            f"color: {TEXT}; background: {_rgba(BTN2, 220)}; padding: 2px 9px; border-radius: 9px; font-weight: 700; font-size: 11px;"
        )
        agent_card, agent_body = self._make_card("Agent Approvals", "Pending requests", extra=self._approval_badge)
        agent_lay = QVBoxLayout(agent_body)
        agent_lay.setContentsMargins(0, 0, 0, 0)
        agent_lay.setSpacing(6)
        self.approval_scroll = QScrollArea()
        self.approval_scroll.setWidgetResizable(True)
        self.approval_scroll.setMaximumHeight(150)
        self.approval_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.approval_inner = QWidget()
        self.approval_lay = QVBoxLayout(self.approval_inner)
        self.approval_lay.setContentsMargins(0, 0, 0, 0)
        self.approval_lay.setSpacing(6)
        self.approval_lay.addStretch(1)
        self.approval_scroll.setWidget(self.approval_inner)
        agent_lay.addWidget(self.approval_scroll)
        right_lay.addWidget(agent_card)

        # ---- App launcher
        apps_card, apps_body = self._make_card("App Launcher", "Alt+1–9 to launch")
        apps_lay = QVBoxLayout(apps_body)
        apps_lay.setContentsMargins(0, 0, 0, 0)
        apps_lay.setSpacing(8)
        self.app_filter_var = QLineEdit()
        self.app_filter_var.setPlaceholderText("Filter apps…")
        self.app_filter_var.textChanged.connect(self._apply_app_filter)
        apps_lay.addWidget(self.app_filter_var)
        self.app_listbox = QListWidget()
        self.app_listbox.setMinimumHeight(104)
        self.app_listbox.itemDoubleClicked.connect(lambda _: self._open_selected_app())
        apps_lay.addWidget(self.app_listbox, 1)
        right_lay.addWidget(apps_card)

        right_scroll.setWidget(right)
        return right_scroll

    def _build_status_bar(self) -> QFrame:
        status = QFrame()
        status.setObjectName("Shell")
        status_lay = QHBoxLayout(status)
        status_lay.setContentsMargins(16, 8, 16, 8)
        status_lay.setSpacing(14)

        status_lay.addWidget(self._section_label("System"))

        self.cpu_bar = MeterBar(CYAN)
        self.mem_bar = MeterBar(INDIGO)
        self.cpu_status = QLabel("--%")
        self.ram_status = QLabel("--%")
        self.net_status = QLabel("↓-- ↑-- KB/s")

        for bar, lbl, name in ((self.cpu_bar, self.cpu_status, "CPU"), (self.mem_bar, self.ram_status, "MEM")):
            cell = QHBoxLayout()
            cell.setSpacing(7)
            cap = QLabel(name)
            cap.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
            cell.addWidget(cap)
            cell.addWidget(bar)
            lbl.setStyleSheet(f"color: {TEXT}; font-size: 11.5px; font-weight: 600;")
            cell.addWidget(lbl)
            wrap = QWidget()
            wrap.setLayout(cell)
            status_lay.addWidget(wrap)

        self.net_status.setStyleSheet(f"color: {TEXT}; font-size: 11.5px; font-weight: 600;")
        status_lay.addWidget(self.net_status)
        status_lay.addStretch(1)

        self.status_hint = QLabel("Say “Jarvis” + your command   ·   Ctrl+Shift+J summons the mini HUD")
        self.status_hint.setObjectName("MetaLabel")
        status_lay.addWidget(self.status_hint)
        return status

    def _show_more_menu(self) -> None:
        self._more_menu.exec(self._more_btn.mapToGlobal(self._more_btn.rect().bottomLeft()))

    def _refresh_status_meters(self) -> None:
        try:
            snap = self._monitor.snapshot()
            self.cpu_status.setText(f"{snap.cpu_percent:.0f}%")
            self.ram_status.setText(f"{snap.memory_percent:.0f}%")
            self.net_status.setText(f"↓{snap.net_recv_kbps:.1f} ↑{snap.net_sent_kbps:.1f} KB/s")
            self.cpu_bar.set_value(snap.cpu_percent)
            self.mem_bar.set_value(snap.memory_percent)
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
        apply_mini_palette(PALETTES[self._theme])
        self.mini.apply_palette(PALETTES[self._theme])

    def _cycle_theme(self) -> None:
        idx = THEME_ORDER.index(_current_theme)
        self._theme = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        _apply_palette(self._theme)
        self.act_theme.setText(f"THEME · {self._theme.upper()}")
        self._rebuild_ui()
        self._save_prefs()

    def _toggle_gfx(self) -> None:
        self._gfx_full = not self._gfx_full
        self.backdrop.set_gfx(self._gfx_full)
        self._refresh_gfx_button()
        self._save_prefs()

    def _refresh_gfx_button(self) -> None:
        self.act_gfx.setText(f"GRAPHICS · {'FULL' if self._gfx_full else 'LITE'}")

    def _apply_background(self, mode: str, value: str = "") -> None:
        self._bg = {"mode": mode, "value": value}
        self.backdrop.set_bg(mode, value)
        self._save_prefs()

    def _open_backgrounds(self) -> None:
        self._show_backgrounds_dialog()

    def _show_backgrounds_dialog(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — Backgrounds")
        win.resize(640, 420)
        lay = QVBoxLayout(win)

        head = QHBoxLayout()
        title = QLabel("BACKGROUND SCENE")
        title.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: 800; letter-spacing: 0.5px;")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        mode_row = QHBoxLayout()
        current_mode = self._bg.get("mode", "aurora")
        for name, label in [("aurora", "Aurora scene"), ("gradient", "Gradient"), ("image", "Static image")]:
            btn = self._chip(label, lambda m=name: self._apply_bg_and_refresh_dialog(win, m))
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
                sw.mousePressEvent = lambda _e, idx=i: self._apply_background("gradient", str(idx))  # type: ignore[method-assign, misc]
                grid.addWidget(sw)
            body_lay.addLayout(grid)
        elif current_mode == "image":
            browse = self._chip("BROWSE…", lambda: self._pick_background_image(win))
            body_lay.addWidget(browse)
            files = list_background_images(_backgrounds_dir())
            if not files:
                hint = QLabel("No images yet — pick one above, or drop a .png/.gif in data/backgrounds/.")
                hint.setObjectName("MetaLabel")
                body_lay.addWidget(hint)
            grid = QHBoxLayout()
            for item in files:
                btn = self._chip(item["name"], lambda n=item["name"]: self._apply_background("image", n))
                if self._bg.get("value") == item["name"]:
                    btn.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
                grid.addWidget(btn)
            body_lay.addLayout(grid)
        else:
            hint = QLabel("Animated aurora scene with drifting particles. Toggle MENU → GRAPHICS for more/fewer particles.")
            hint.setObjectName("MetaLabel")
            body_lay.addWidget(hint)

        lay.addWidget(body, 1)
        done = self._chip("DONE", win.accept, primary=True)
        lay.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        win.exec()

    def _apply_bg_and_refresh_dialog(self, win: QDialog, mode: str) -> None:
        self._apply_background(mode)
        win.accept()
        self._show_backgrounds_dialog()

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
        tag_colors = {
            "CMD": CYAN, "OK": GREEN, "WARN": AMBER, "ERR": RED,
            "SYS": INDIGO, "JARVIS": GREEN,
        }
        tag = tag_colors.get(kind, MUTED)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.terminal.append(
            f'<span style="color:{MUTED}">{ts}</span> '
            f'<span style="color:{tag}; font-weight:700">[{kind}]</span> '
            f'<span style="color:{TEXT}">{safe}</span>'
        )
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def _append_feed(self, tag: str, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        color = {
            "feed_cmd": CYAN, "feed_ok": GREEN, "feed_pending": AMBER,
            "feed_err": RED, "feed_sys": MUTED,
        }.get(tag, MUTED)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.feed_text.append(
            f'<span style="color:{MUTED}; font-size:10px">{ts}</span> '
            f'<span style="color:{color}">{safe}</span>'
        )
        self.feed_text.verticalScrollBar().setValue(self.feed_text.verticalScrollBar().maximum())

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.run_button.setEnabled(not busy)
        # While a chat stream is active the ASK button doubles as STOP and must
        # stay clickable; every other busy source disables it as before.
        if not self._chat_streaming:
            self.ask_button.setEnabled(not busy)

    def _next_busy_seq(self) -> int:
        """Allocate the watchdog token for a new command run."""
        self._busy_seq += 1
        if len(self._done_seqs) > 64:
            self._done_seqs = {seq for seq in self._done_seqs if seq > self._busy_seq - 64}
        return self._busy_seq

    def _enqueue(self, item: tuple[str, Any]) -> None:
        """Thread-safe event enqueue that drops the oldest events when saturated.

        Producers are background threads; blocking them on a full queue would
        stall the very workers the HUD is reporting on.
        """
        while True:
            try:
                self._events.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    return

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
        self._append_feed("feed_cmd", f"▸ {command}")
        self.mini.notify_command(command)
        self._set_busy(True)
        self._set_ai_state("processing")
        seq = self._next_busy_seq()

        def watchdog() -> None:
            self._enqueue(("command_timeout", seq))

        deadline = max(5, int(getattr(self.runtime.settings, "command_busy_timeout_seconds", 180)))
        timer = threading.Timer(deadline, watchdog)
        timer.daemon = True

        def worker() -> None:
            try:
                result = self.runtime.orchestrator.execute_command(
                    command=command,
                    source="gui",
                    auto_approve=False,
                    on_step=lambda step: self._enqueue(("agent_step", step)),
                )
                self._enqueue(("command_result", (seq, result.model_dump())))
            except Exception as exc:
                self._enqueue(("error", str(exc)))
            finally:
                timer.cancel()

        timer.start()
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
        if direction > 0:
            idx = min(max(self._cmd_history_index + 1, 0), n - 1)
        else:
            idx = max(min(self._cmd_history_index - 1, n - 1), 0) if self._cmd_history_index >= 0 else 0
        self._cmd_history_index = idx
        self.command_entry.setText(self._cmd_history[idx])

    def eventFilter(self, obj, event) -> bool:
        if obj is self.command_entry and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                self._history_step(-1)
                return True
            if event.key() == Qt.Key.Key_Down:
                self._history_step(1)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ voice

    def _toggle_voice(self) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                if self.runtime.voice.is_running():
                    changed = self.runtime.voice.stop()
                    self._enqueue(("voice", {"running": False, "changed": changed}))
                else:
                    changed = self.runtime.voice.start()
                    self._enqueue(("voice", {"running": True, "changed": changed}))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_voice_button(self) -> None:
        running = self.runtime.voice.is_running()
        try:
            status = self.runtime.voice.status()
        except Exception:
            status = {}
        phase, color, amp, speed = voice_phase(status)
        self.voice_wave.update_status(color, amp, speed)
        self.voice_dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        if not running:
            self.voice_state_label.setText("OFFLINE")
            self.voice_state_label.setStyleSheet(f"color: {MUTED}; font-size: 17px; font-weight: 800; letter-spacing: 2px;")
            self.voice_status_label.setText("Voice engine idle — click to activate")
            self.voice_switch_button.setText("ENABLE MICROPHONE")
            self.voice_switch_button.setStyleSheet("")
            self.online_label.setText("● ONLINE")
            self.online_label.setStyleSheet(
                f"color: {GREEN}; font-size: 11px; font-weight: 700; letter-spacing: 1px;"
                f" padding: 4px 12px; background: {_rgba(GREEN, 26)}; border-radius: 9px;"
            )
        else:
            subs = {
                "LISTENING": "Listening — say “Jarvis” + your command",
                "PROCESSING": "Transcribing your command…",
                "ATTENTION": "Needs attention — check the terminal",
            }
            self.voice_state_label.setText(phase)
            self.voice_state_label.setStyleSheet(f"color: {color}; font-size: 17px; font-weight: 800; letter-spacing: 2px;")
            self.voice_status_label.setText(subs.get(phase, "Microphone live"))
            self.voice_switch_button.setText("DISABLE MICROPHONE")
            self.voice_switch_button.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)};")
            self.online_label.setText("● STREAMING")
            self.online_label.setStyleSheet(
                f"color: {CYAN}; font-size: 11px; font-weight: 700; letter-spacing: 1px;"
                f" padding: 4px 12px; background: {_rgba(CYAN, 26)}; border-radius: 9px;"
            )

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
                self._enqueue(("job_status", payload))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_job(self) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("job_cancel", {})
                self._enqueue(("job_status", payload))
                self._enqueue(("job_msg", payload.get("result_text", "Job task canceled.")))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _resume_job(self) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("job_resume", {})
                self._enqueue(("job_status", payload))
                self._enqueue(("job_msg", payload.get("result_text", "Job task resumed.")))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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
        self.job_state_label.setText(status.upper())
        self.job_state_label.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 800; letter-spacing: 1px;"
            f" padding: 3px 11px; background: {_rgba(color, 26)}; border-radius: 9px;"
        )
        self.job_phase_label.setText(phase)
        if reason and status == "paused":
            self.job_reason_label.setText(f"Paused · {reason}")
            self.job_reason_label.show()
        else:
            self.job_reason_label.hide()
        self.job_stat_labels["APPLIED"].setText(str(applied))
        self.job_stat_labels["SKIPPED"].setText(str(skipped))
        self.job_stat_labels["FAILED"].setText(str(failed))
        totals = payload.get("totals") or (task.get("totals") if isinstance(task, dict) else None) or {}
        for name in ("INTERVIEW", "OFFER", "REJECTED"):
            self.job_stat_labels[name].setText(str(int(totals.get(name.lower(), 0))))
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
                self._enqueue(("approvals", items))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _render_approval_cards(self, items: list[dict[str, Any]]) -> None:
        # takeAt() is typed Optional[QLayoutItem]; narrow before use.
        while (old := self.approval_lay.takeAt(0)) is not None:
            if (w := old.widget()) is not None:
                w.deleteLater()
        self._approval_cards.clear()
        self._pending_ids = [str(a.get("id") or a.get("approval_id") or "") for a in items]
        for a in items:
            card = QFrame()
            card.setStyleSheet(f"background: {_rgba(INNER, 205)}; border: 1px solid {_rgba(STROKE, 165)}; border-radius: 8px;")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(4)
            prompt = str(a.get("prompt") or a.get("command") or "")
            p = QLabel(prompt[:120] or "(no prompt)")
            p.setWordWrap(True)
            p.setStyleSheet(f"color: {TEXT}; font-size: 11.5px; font-weight: 700;")
            lay.addWidget(p)
            detail = str(a.get("reason") or a.get("summary") or a.get("description") or "")
            if detail:
                d = QLabel(detail[:160])
                d.setWordWrap(True)
                d.setStyleSheet(f"color: {MUTED}; font-size: 10.5px;")
                lay.addWidget(d)
            act = QHBoxLayout()
            act.setSpacing(6)
            approval_id = str(a.get("id") or a.get("approval_id") or "")
            approve = self._button("APPROVE", lambda aid=approval_id: self._approve_id(aid), kind="tertiary", small=True)
            approve.setStyleSheet(f"color: {GREEN}; background: {_rgba(OK, 220)}; border-color: {_rgba(OK, 240)};")
            reject = self._button("REJECT", lambda aid=approval_id: self._reject_id(aid), kind="tertiary", small=True)
            reject.setStyleSheet(f"color: {RED}; background: {_rgba(DANGER, 220)}; border-color: {_rgba(DANGER, 240)};")
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
            f"color: {'#021018' if count else TEXT}; background: {_rgba(AMBER if count else BTN2, 220)};"
            f" padding: 2px 9px; border-radius: 9px; font-weight: 700; font-size: 11px;"
        )
        if len(items) > self._last_pending_count and items:
            self._toaster.notify("Approval pending", f"{len(items)} action(s) waiting for you.", AMBER, on_click=self._raise_main_window)
        self._last_pending_count = len(items)

    def _approve_id(self, approval_id: str) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                res = self.runtime.orchestrator.approve(approval_id)
                self._enqueue(("approval_result", res.model_dump()))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _reject_id(self, approval_id: str) -> None:
        self._set_busy(True)

        def worker() -> None:
            try:
                res = self.runtime.orchestrator.reject(approval_id)
                self._enqueue(("approval_result", res.model_dump()))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ diagnose

    def _diagnose_system(self) -> None:
        self._append_terminal("SYS", "Running system diagnosis…")

        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("diagnose_system", {})
                self._enqueue(("diagnosis", payload))
            except Exception as exc:
                self._enqueue(("diagnosis_error", str(exc)))

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
            self._enqueue(("hub_event", (topic, payload)))

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
        self._set_ai_state("idle")
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
        rendered_agent_steps = 0
        dropped_agent_steps = 0
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if event == "command_result":
                seq, payload = payload
                self._done_seqs.add(seq)
                self._handle_command_result(payload)
            elif event == "command_timeout":
                seq = int(payload)
                if seq not in self._done_seqs:
                    deadline = max(5, int(getattr(self.runtime.settings, "command_busy_timeout_seconds", 180)))
                    self._set_busy(False)
                    self._set_ai_state("idle")
                    self._append_terminal("ERR", f"Command exceeded the {deadline}s busy deadline; input re-enabled. It may still be running in the background.")
                    self._append_feed("feed_err", f"✗ command timed out after {deadline}s")
                    self._toaster.notify("Command timeout", f"No result after {deadline}s.", RED)
            elif event == "agent_step":
                # Cap per-tick widget churn during agent step storms.
                if rendered_agent_steps >= _AGENT_STEP_BUDGET_PER_TICK:
                    dropped_agent_steps += 1
                    continue
                rendered_agent_steps += 1
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
            elif event == "tray_show":
                self._raise_main_window()
            elif event == "tray_mini":
                self.mini.show()
            elif event == "tray_exit":
                self._request_quit()
            elif event == "approval_result":
                self._set_busy(False)
                self._set_ai_state("idle")
                self._append_terminal("OK", payload.get("message", ""))
                self._refresh_approvals()
            elif event == "app_launcher":
                self._handle_app_launcher(payload)
            elif event == "chat_stream_chunk":
                self._append_chat_stream_chunk(str(payload))
            elif event == "chat_stream_done":
                self._handle_chat_done(dict(payload or {}))
            elif event == "chat_stream_error":
                self._handle_chat_error(str(payload))
            elif event == "job_status":
                self._update_job_panel(payload)
            elif event == "job_msg":
                self._set_busy(False)
                self._set_ai_state("idle")
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
                self._flash_ai_error()
                self._append_terminal("ERR", str(payload))
                self._toaster.notify("JARVIS error", str(payload), RED)
        if dropped_agent_steps:
            self._append_feed("feed_pending", f"… {dropped_agent_steps} agent steps collapsed (rate-limited)")

    # ------------------------------------------------------------ app launcher

    def _refresh_app_launcher(self) -> None:
        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("list_known_apps", {})
                self._enqueue(("app_launcher", payload))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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

    def _append_chat(self, role: str, text: str, images: list[str] | None = None) -> None:
        self._ensure_chat_visible()
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ts = datetime.now().strftime("%H:%M")
        if role == "user":
            img_html = ""
            for img in images or []:
                src = Path(img).as_posix()
                img_html += f'<img src="file:///{src}" width="220"><br>'
            self.chat_text.append(
                f'<table width="100%" cellspacing="0" cellpadding="6"><tr>'
                f'<td bgcolor="{ENTRY}">'
                f'<span style="color:{CYAN}; font-size:10px; font-weight:700;">YOU · {ts}</span><br>'
                f"{img_html}"
                f'<span style="color:{TEXT}; font-size:13px;">{safe}</span>'
                f"</td></tr></table>"
            )
            self.chat_text.append("<br>")
        else:
            self.chat_text.append(f'<span style="color:{INDIGO}; font-weight:700; font-size:10px;">JARVIS · {ts}</span>')
            self.chat_text.append(f'<span style="color:{TEXT}; font-size:13px;">{safe}</span>')
            self.chat_text.append("<br>")
        self.chat_text.verticalScrollBar().setValue(self.chat_text.verticalScrollBar().maximum())

    def _start_chat_stream(self) -> None:
        self._ensure_chat_visible()
        self._chat_responding = False
        self._chat_streaming = True
        self._chat_stream_text = []
        ts = datetime.now().strftime("%H:%M")
        self.chat_text.append(
            f'<span style="color:{INDIGO}; font-weight:700; font-size:10px;">JARVIS · {ts}</span><br>'
        )
        self._chat_stream_start_offset = len(self.chat_text.toPlainText())
        self.chat_text.verticalScrollBar().setValue(self.chat_text.verticalScrollBar().maximum())

    def _append_chat_stream_chunk(self, chunk: str) -> None:
        if not self._chat_streaming:
            return
        self._chat_stream_text.append(chunk)
        if not self._chat_responding:
            self._chat_responding = True
            self._set_ai_state("responding")
        safe = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        cursor = self.chat_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(f'<span style="color:{TEXT}; font-size:13px;">{safe}</span>')
        self.chat_text.setTextCursor(cursor)
        self.chat_text.verticalScrollBar().setValue(self.chat_text.verticalScrollBar().maximum())

    def _finish_chat_stream_text(self, text: str, stopped: bool) -> None:
        """Swap the streamed plain text for the rendered Markdown version."""
        if not self._chat_streaming:
            return
        html = markdown_to_html(text)
        suffix = f' <span style="color:{AMBER}; font-size:10px;">· stopped</span>' if stopped else ""
        start = int(getattr(self, "_chat_stream_start_offset", -1))
        end = len(self.chat_text.toPlainText())
        cursor = self.chat_text.textCursor()
        if start >= 0 and end > start:
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(html + suffix + "<br><br>")
        else:
            self.chat_text.append(html + suffix)
            self.chat_text.append("<br>")
        self._chat_stream_start_offset = -1
        self._chat_stream_text = []
        self._chat_streaming = False
        self.ask_button.setText("ASK")
        self.ask_button.setEnabled(True)
        self._set_ai_state("idle")

    # -- ask / stop / retry / clear -------------------------------------------

    def _on_ask_clicked(self) -> None:
        if self._chat_streaming:
            self._stop_chat_stream()
            return
        text = self.chat_entry.text().strip()
        if not text:
            return
        self.chat_entry.clear()
        self._ask_chatbot(text)

    def _ask_chatbot(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._chat_streaming or (self._busy and not self._chat_streaming):
            return

        route = classify_user_input(text)
        self._last_user_text = text
        images = list(self._pending_images)
        self._pending_images = []
        self._refresh_image_button()
        self._append_chat("user", text, images)

        if route == "command":
            self.chat_text.append(
                f'<span style="color:{MUTED}; font-size:11px;">→ routed to command system</span><br>'
            )
            self._run_command_text(text)
            return

        window = max(2, int(getattr(self.runtime.settings, "jarvis_chat_history_window", 12)))
        history = build_history_from_memory(self.runtime.memory, window)
        self._set_busy(True)
        self._set_ai_state("thinking")
        self._chat_cancel = threading.Event()
        self.ask_button.setText("STOP")
        self._start_chat_stream()

        def worker() -> None:
            cancel = self._chat_cancel or threading.Event()
            try:
                for event in self._chat_service.stream_chat(text, history, cancel):
                    kind = event.get("type")
                    if kind == "delta":
                        self._enqueue(("chat_stream_chunk", str(event.get("text", ""))))
                    elif kind == "done":
                        self._enqueue(
                            (
                                "chat_stream_done",
                                {
                                    "text": str(event.get("text", "")),
                                    "stopped": bool(event.get("stopped")),
                                    "meta": dict(event.get("meta") or {}),
                                },
                            )
                        )
                    elif kind == "error":
                        self._enqueue(("chat_stream_error", str(event.get("message", "Chat failed."))))
            except Exception as exc:  # provider construction errors land here
                self._enqueue(("chat_stream_error", f"Unexpected chat error: {exc}"))

        threading.Thread(target=worker, daemon=True, name="jarvis-chat").start()

    def _stop_chat_stream(self) -> None:
        if self._chat_cancel is not None:
            self._chat_cancel.set()
        self.ask_button.setText("…")
        self.ask_button.setEnabled(False)

    def _retry_last_chat(self) -> None:
        if not self._last_user_text:
            return
        self._ask_chatbot(self._last_user_text)

    def _clear_conversation(self) -> None:
        was_streaming = self._chat_streaming
        if was_streaming:
            self._stop_chat_stream()
        self._chat_streaming = False
        self._chat_stream_start_offset = -1
        self._chat_stream_text = []
        self._chat_cancel = None
        self.chat_text.clear()
        self.ai_stack.setCurrentIndex(0)
        self.ask_button.setText("ASK")
        self.ask_button.setEnabled(True)
        self._set_ai_state("idle")
        if was_streaming and self._busy:
            self._set_busy(False)

    def _attach_chat_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach images",
            "",
            "Image files (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*.*)",
        )
        for path in paths[:3]:
            if path and path not in self._pending_images:
                self._pending_images.append(path)
        self._refresh_image_button()

    def _refresh_image_button(self) -> None:
        n = len(self._pending_images)
        self.chat_image_button.setText(f"IMAGE ×{n}" if n else "IMAGE")

    def _handle_chat_done(self, payload: dict[str, Any]) -> None:
        text = str(payload.get("text") or "")
        stopped = bool(payload.get("stopped"))
        meta = dict(payload.get("meta") or {})
        self._finish_chat_stream_text(text, stopped)
        self._set_busy(False)
        self._update_chat_status(meta)
        if meta:
            logger_note = "fallback" if meta.get("fallback_used") else "ok"
            self._append_terminal(
                "SYS",
                f"chat {meta.get('provider')} · {meta.get('model')} — "
                f"{meta.get('first_token_ms', -1)}ms first token, {meta.get('total_ms', -1)}ms total [{logger_note}]",
            )
            if meta.get("fallback_used"):
                self._toaster.notify("Chat fallback", f"Served by {meta.get('provider')} · {meta.get('model')}", AMBER, duration_ms=3500)
        if text and not stopped:
            try:
                self.runtime.memory.log_command(source="chat", command=self._last_user_text or "(chat)", response_text=text)
            except Exception:
                pass

    def _handle_chat_error(self, message: str) -> None:
        self._chat_streaming = False
        self._chat_stream_start_offset = -1
        self._chat_stream_text = []
        self.ask_button.setText("ASK")
        self.ask_button.setEnabled(True)
        self._set_busy(False)
        self._flash_ai_error()
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.chat_text.append(
            f'<table width="100%" cellspacing="0" cellpadding="6"><tr>'
            f'<td bgcolor="{DANGER}"><span style="color:{RED}; font-size:12px; font-weight:700;">CHAT ERROR</span><br>'
            f'<span style="color:{TEXT}; font-size:12px;">{safe}</span>'
            f"</td></tr></table><br>"
        )
        self.chat_text.verticalScrollBar().setValue(self.chat_text.verticalScrollBar().maximum())

    def _update_chat_status(self, meta: dict[str, Any]) -> None:
        if not meta:
            return
        ttft = meta.get("first_token_ms", -1)
        total = meta.get("total_ms", -1)
        bits = [f"{meta.get('provider', '?')}", f"{meta.get('model', '?')}"]
        if isinstance(ttft, int) and ttft >= 0:
            bits.append(f"{ttft}ms TTFT")
        if isinstance(total, int) and total >= 0:
            bits.append(f"{total / 1000:.1f}s")
        if meta.get("fallback_used"):
            bits.append("fallback")
        color = AMBER if meta.get("fallback_used") else MUTED
        self.chat_status_label.setText(" · ".join(bits))
        self.chat_status_label.setStyleSheet(f"color: {color}; font-size: 11px;")


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
        sc = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        sc.activated.connect(self._clear_conversation)

    # ------------------------------------------------------------ scheduler

    def _scheduler_on_run(self, job: dict[str, Any], status: str, text: str) -> None:
        self._enqueue(("sched_run", (job, status, text)))
        announce = self._scheduler_announce
        if announce is not None:
            try:
                announce(job, status, text)
            except Exception:
                pass
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

        add_card, add_body = self._make_card("New schedule", "Natural recurrence language")
        add_lay = QVBoxLayout(add_body)
        add_lay.setContentsMargins(0, 0, 0, 0)
        add_lay.setSpacing(6)
        row1 = QHBoxLayout()
        cmd_entry = QLineEdit()
        cmd_entry.setPlaceholderText("command…")
        rec_entry = QLineEdit()
        rec_entry.setPlaceholderText("recurrence (e.g. daily at 09:00)…")
        row1.addWidget(cmd_entry, 1)
        row1.addWidget(rec_entry, 1)
        add_lay.addLayout(row1)
        hint = QLabel("e.g. daily at 09:00  ·  hourly  ·  every 30 minutes  ·  weekly on monday at 10:00")
        hint.setObjectName("MetaLabel")
        add_lay.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._chip("SCHEDULE", lambda: self._add_schedule(cmd_entry, rec_entry), primary=True))
        add_lay.addLayout(btn_row)
        lay.addWidget(add_card)

        list_card, list_body = self._make_card("Scheduled jobs", "")
        list_lay = QVBoxLayout(list_body)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(6)
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(self._chip("REFRESH", self._scheduler_load))
        list_lay.addLayout(head)
        self._sched_listbox = QListWidget()
        list_lay.addWidget(self._sched_listbox)
        lay.addWidget(list_card, 1)

        actions = QHBoxLayout()
        self._sched_status = QLabel("")
        self._sched_status.setObjectName("MetaLabel")
        actions.addWidget(self._sched_status, 1)
        actions.addWidget(self._chip("ENABLE / DISABLE", self._toggle_schedule))
        actions.addWidget(self._chip("DELETE", self._delete_schedule))
        lay.addLayout(actions)

        self._sched_win: QDialog | None = win
        self._sched_jobs: list[dict[str, Any]] = []
        self._scheduler_load()
        poll = QTimer(win)
        poll.timeout.connect(self._scheduler_load)
        poll.start(4000)

        def _on_close() -> None:
            poll.stop()
            self._sched_win = None

        win.finished.connect(_on_close)
        win.exec()

    def _scheduler_load(self) -> None:
        def worker() -> None:
            try:
                jobs = self.runtime.scheduler.list_jobs()
                self._enqueue(("sched_list", jobs))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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
                self._enqueue(("sched_added", job.get("id", "")))
                self._enqueue(("sched_list", self.runtime.scheduler.list_jobs()))
            except Exception as exc:
                self._enqueue(("sched_error", str(exc)))

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
                self._enqueue(("sched_list", self.runtime.scheduler.list_jobs()))
            except Exception as exc:
                self._enqueue(("sched_error", str(exc)))

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
                self._enqueue(("sched_list", self.runtime.scheduler.list_jobs()))
            except Exception as exc:
                self._enqueue(("sched_error", str(exc)))

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
        status.setObjectName("MetaLabel")
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
                self._enqueue(("history_data", data))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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
        title.setStyleSheet(f"color: {CYAN}; font-size: 14px; font-weight: 800; letter-spacing: 1px;")
        head.addWidget(title, 1)
        head.addWidget(self._chip("REFRESH", self._tasks_load))
        lay.addLayout(head)

        list_card, list_body = self._make_card("Tracked tasks", "")
        list_lay = QVBoxLayout(list_body)
        list_lay.setContentsMargins(0, 0, 0, 0)
        self._tasks_listbox = QListWidget()
        self._tasks_listbox.currentRowChanged.connect(lambda _: self._show_task_detail())
        list_lay.addWidget(self._tasks_listbox)
        lay.addWidget(list_card, 1)

        detail_card, detail_body = self._make_card("Task detail", "")
        detail_lay = QVBoxLayout(detail_body)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        self._task_detail_text = QTextEdit()
        self._task_detail_text.setReadOnly(True)
        detail_lay.addWidget(self._task_detail_text)
        lay.addWidget(detail_card, 2)

        status = QLabel("")
        status.setObjectName("MetaLabel")
        lay.addWidget(status)
        self._tasks_status = status

        self._tasks_win: QDialog | None = win
        self._tasks_items: list[dict[str, Any]] = []
        self._tasks_load()
        poll = QTimer(win)
        poll.timeout.connect(self._tasks_load)
        poll.start(4000)

        def _on_close() -> None:
            poll.stop()
            self._tasks_win = None

        win.finished.connect(_on_close)
        win.exec()

    def _tasks_load(self) -> None:
        def worker() -> None:
            try:
                payload = self.runtime.registry.execute("task_list", {"limit": 50})
                self._enqueue(("tasks_list", payload))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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
                self._enqueue(("task_detail", payload))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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
        title.setStyleSheet(f"color: {CYAN}; font-size: 14px; font-weight: 800; letter-spacing: 1px;")
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
        status.setObjectName("MetaLabel")
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
                self._enqueue(("memory_data", {"episodes": episodes, "knowledge": knowledge}))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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
                self._enqueue(("memory_search", payload))
            except Exception as exc:
                self._enqueue(("error", str(exc)))

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

    # ------------------------------------------------------------ voice settings

    KOKORO_VOICES = [
        "af_bella", "af_heart", "af_nicole", "af_sarah",
        "am_adam", "am_michael",
        "bf_emma", "bf_isabella",
        "bm_george", "bm_lewis",
    ]
    KOKORO_LANGS = [
        ("American English", "a"),
        ("British English", "b"),
        ("Japanese", "j"),
        ("Chinese", "z"),
    ]

    def _open_voice_settings(self) -> None:
        voice = self.runtime.voice
        speaker = voice.speaker

        win = QDialog(self)
        win.setWindowTitle("JARVIS — Voice Settings")
        win.resize(520, 540)
        outer = QVBoxLayout(win)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        eng_group = QGroupBox("TTS ENGINE")
        eng_lay = QHBoxLayout(eng_group)
        engine_buttons: dict[str, QRadioButton] = {}
        current_mode = speaker.status().get("mode", "sapi")
        for mode_name in ("kokoro", "sapi", "pyttsx3"):
            rb = QRadioButton(mode_name.upper())
            rb.setChecked(mode_name == current_mode)
            engine_buttons[mode_name] = rb
            eng_lay.addWidget(rb)

        def on_engine_change() -> None:
            for mode, rb in engine_buttons.items():
                if rb.isChecked():
                    try:
                        voice.set_speaker_mode(mode)
                    except Exception:
                        pass
                    break

        for rb in engine_buttons.values():
            rb.toggled.connect(on_engine_change)
        lay.addWidget(eng_group)

        voice_group = QGroupBox("KOKORO VOICE NAME")
        voice_lay = QHBoxLayout(voice_group)
        voice_combo = QComboBox()
        voice_combo.setEditable(True)
        voice_combo.addItems(self.KOKORO_VOICES)
        current_voice = speaker.status().get("kokoro_voice", "af_bella")
        idx = voice_combo.findText(current_voice)
        if idx >= 0:
            voice_combo.setCurrentIndex(idx)
        else:
            voice_combo.setEditText(current_voice)
        voice_lay.addWidget(voice_combo, 1)

        def on_voice_change(text: str) -> None:
            speaker.set_kokoro_voice(text)

        voice_combo.currentTextChanged.connect(on_voice_change)
        lay.addWidget(voice_group)

        lang_group = QGroupBox("KOKORO LANGUAGE")
        lang_lay = QHBoxLayout(lang_group)
        lang_combo = QComboBox()
        current_lang = speaker.status().get("kokoro_lang_code", "a")
        for label, code in self.KOKORO_LANGS:
            lang_combo.addItem(label, code)
        for i in range(lang_combo.count()):
            if lang_combo.itemData(i) == current_lang:
                lang_combo.setCurrentIndex(i)
                break
        lang_lay.addWidget(lang_combo)

        def on_lang_change(index: int) -> None:
            code = lang_combo.itemData(index)
            if code:
                speaker._kokoro_lang_code = code
                if speaker._kokoro_pipeline is not None:
                    speaker._init_kokoro()

        lang_combo.currentIndexChanged.connect(on_lang_change)
        lay.addWidget(lang_group)

        speed_group = QGroupBox("PLAYBACK SPEED")
        speed_lay = QHBoxLayout(speed_group)
        speed_slider = QSlider(Qt.Orientation.Horizontal)
        speed_slider.setRange(50, 200)
        current_speed = speaker.status().get("kokoro_speed", 1.0) if hasattr(speaker, '_kokoro_speed') else 1.0
        speed_slider.setValue(int(current_speed * 100))
        speed_label = QLabel(f"{current_speed:.1f}x")
        speed_label.setFixedWidth(40)
        speed_lay.addWidget(speed_slider, 1)
        speed_lay.addWidget(speed_label)

        def on_speed_change(val: int) -> None:
            s = val / 100.0
            speaker.set_kokoro_speed(s)
            speed_label.setText(f"{s:.1f}x")

        speed_slider.valueChanged.connect(on_speed_change)
        lay.addWidget(speed_group)

        out_group = QGroupBox("AUDIO OUTPUT DEVICE")
        out_lay = QHBoxLayout(out_group)
        out_combo = QComboBox()
        out_devices = voice.list_output_devices()
        selected_out = out_devices.get("selected_index")
        for dev in out_devices.get("devices", []):
            name = dev.get("name", f"Device {dev.get('index', '?')}")
            out_combo.addItem(name, dev.get("index"))
        if selected_out is not None:
            for i in range(out_combo.count()):
                if out_combo.itemData(i) == selected_out:
                    out_combo.setCurrentIndex(i)
                    break
        out_lay.addWidget(out_combo, 1)

        def on_out_change(index: int) -> None:
            oidx = out_combo.itemData(index)
            voice.set_output_device(oidx)

        out_combo.currentIndexChanged.connect(on_out_change)
        lay.addWidget(out_group)

        mic_group = QGroupBox("MICROPHONE")
        mic_lay = QHBoxLayout(mic_group)
        mic_combo = QComboBox()
        mic_devices = voice.list_input_devices()
        for dev in mic_devices.get("devices", []):
            mic_combo.addItem(dev.get("name", f"Mic {dev.get('index', '?')}"), dev.get("index"))
        mic_lay.addWidget(mic_combo, 1)

        def on_mic_change(index: int) -> None:
            midx = mic_combo.itemData(index)
            voice.set_input_device(midx)

        mic_combo.currentIndexChanged.connect(on_mic_change)
        lay.addWidget(mic_group)

        btn_row = QHBoxLayout()

        def test_voice() -> None:
            voice._speak_async("Hello Boss Sahu, testing voice.")

        test_btn = self._chip("SPEAK TEST", test_voice, primary=True)
        btn_row.addWidget(test_btn)

        btn_row.addStretch(1)
        close_btn = self._chip("CLOSE", win.close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        lay.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)
        win.exec()

    # ------------------------------------------------------------ help

    def _open_help(self) -> None:
        win = QDialog(self)
        win.setWindowTitle("JARVIS — What can I do?")
        win.resize(760, 600)
        lay = QVBoxLayout(win)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 11))
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
        self.act_autostart.setText(f"AUTOSTART · {'ON' if on else 'OFF'}")

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
        self.act_tray.setText("TRAY · ON")
        self._toaster.notify(
            "JARVIS in tray", "HUD is running in the tray.\nUse the JARVIS icon or Ctrl+Shift+J to bring it back.", GREEN
        )
        self.hide()

    def _tray_show(self) -> None:
        # pystray invokes this on its own thread - never touch Qt from here.
        self._enqueue(("tray_show", None))

    def _tray_mini(self) -> None:
        self._enqueue(("tray_mini", None))

    def _tray_exit(self) -> None:
        self._enqueue(("tray_exit", None))

    def _request_quit(self) -> None:
        self._on_close()
        self._app.quit()

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
            apply_mini_palette(PALETTES[self._theme])
            self.act_theme.setText(f"THEME · {self._theme.upper()}")
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
        self._app.quit()
        event.accept()

    def _on_close(self) -> None:
        # Persist UI state and close windows first so the HUD disappears
        # instantly; remaining teardown happens behind a dead window.
        self._save_ui_config()
        try:
            self.mini.close()
        except Exception:
            pass
        try:
            self.runtime.voice.stop(timeout=2.0)
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

    def run(self) -> None:
        self.show()
        self._app.exec()


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


def _qt_message_handler(msg_type, context, message):
    """Suppress noisy Qt messages (e.g. DPI awareness errors)."""
    if "SetProcessDpiAwarenessContext" in message:
        return
    from PySide6.QtCore import QtMsgType

    if msg_type == QtMsgType.QtWarningMsg:
        import sys

        print(f"Qt WARNING: {message}", file=sys.stderr)
    elif msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        import sys

        print(f"Qt CRITICAL: {message}", file=sys.stderr)


def main() -> None:
    _enable_dpi_awareness()
    from app.core.single_instance import SingleInstance

    if not SingleInstance("hud").acquire():
        _signal_running_instance()  # another HUD is running; ask it to come to the front
        return
    from PySide6.QtCore import qInstallMessageHandler

    qInstallMessageHandler(_qt_message_handler)
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    window = JarvisHudApp(app)
    try:
        window.run()
    except Exception:
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
