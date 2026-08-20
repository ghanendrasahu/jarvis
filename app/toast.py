from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

BG = "#020617"
STROKE = "#1e2c52"
TEXT = "#dbeafe"
MUTED = "#8ea6ca"
PANEL = "#0a1529"


class Toaster(QWidget):
    """Frameless, topmost toast shown in the corner of the main window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._on_click: Callable[[], None] | None = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._dismiss)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)

        self.frame = QFrame(self)
        self.frame.setObjectName("Toast")
        shadow = QGraphicsDropShadowEffect(self.frame)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 6)
        shadow.setColor("#000000")
        self.frame.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_label = QLabel(self.frame)
        self.title_label.setObjectName("ToastTitle")
        head.addWidget(self.title_label, 1)
        close = QLabel("✕", self.frame)
        close.setObjectName("ToastClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.mousePressEvent = lambda _e: self._dismiss()
        head.addWidget(close)
        lay.addLayout(head)

        self.msg_label = QLabel(self.frame)
        self.msg_label.setObjectName("ToastMsg")
        self.msg_label.setWordWrap(True)
        lay.addWidget(self.msg_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        self.hide()

    def notify(
        self,
        title: str,
        message: str,
        color: str = "#fbbf24",
        duration_ms: int = 6500,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self._dismiss()
        self.title_label.setText(title)
        self.title_label.setStyleSheet(f"color: {color};")
        self.msg_label.setText(message)
        self._on_click = on_click
        self.adjustSize()
        self._position()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()
        self._hide_timer.start(duration_ms)

    def _position(self) -> None:
        parent = self.parentWidget()
        if parent is not None and parent.window() is not None:
            win = parent.window()
            x = win.x() + win.width() - self.width() - 18
            y = win.y() + 14
            self.move(max(0, x), max(0, y))

    def mouseReleaseEvent(self, event) -> None:
        cb = self._on_click
        if cb is not None:
            self._on_click = None
            self._dismiss()
            cb()
        else:
            super().mouseReleaseEvent(event)

    def _dismiss(self) -> None:
        self._hide_timer.stop()
        self._fade.stop()
        self.hide()