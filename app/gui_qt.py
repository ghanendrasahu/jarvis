from __future__ import annotations

import random
import sys
import threading
import os
from datetime import datetime

import qtawesome as qta
from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)
import psutil

from app.bootstrap import build_runtime

COLORS = {
    "bg": "#020617",
    "bg2": "#081120",
    "panel": "#0F172A",
    "glass": "rgba(15, 23, 42, 0.72)",
    "cyan": "#38BDF8",
    "blue": "#3B82F6",
    "purple": "#8B5CF6",
    "green": "#22C55E",
    "amber": "#F59E0B",
    "text": "#DCE9FF",
    "muted": "#8AA4C9",
}
LITE_MODE = os.getenv("JARVIS_QT_LITE", "1").strip() != "0"


def neon_shadow(color: str, blur: int = 28, y: int = 0) -> QGraphicsDropShadowEffect:
    effect = QGraphicsDropShadowEffect()
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(color))
    return effect


class GlowButton(QPushButton):
    def __init__(self, text: str, icon_name: str | None = None, accent: str = COLORS["blue"]):
        super().__init__(text)
        self.accent = accent
        if icon_name:
            self.setIcon(qta.icon(icon_name, color=COLORS["cyan"]))
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(46)
        if not LITE_MODE:
            self.setGraphicsEffect(neon_shadow(accent, blur=18))


class PulseDot(QLabel):
    def __init__(self, color: str = COLORS["green"]):
        super().__init__("  ")
        self._v = 0.0
        self.color = color
        self.setFixedSize(14, 14)
        self.anim = QPropertyAnimation(self, b"pulse")
        self.anim.setDuration(1500)
        self.anim.setStartValue(0.25)
        self.anim.setEndValue(1.0)
        self.anim.setLoopCount(-1)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.start()

    def get_pulse(self) -> float:
        return self._v

    def set_pulse(self, value: float) -> None:
        self._v = value
        alpha = int(120 + (100 * value))
        self.setStyleSheet(
            f"background: rgba(34,197,94,{alpha}); border-radius: 7px; border: 1px solid rgba(56,189,248,0.45);"
        )

    pulse = Property(float, get_pulse, set_pulse)


class WaveformWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(28)
        layout.addWidget(self.bar)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(120)

    def tick(self) -> None:
        self.bar.setValue(random.randint(20, 95))


class LiveChart(QWidget):
    def __init__(self, color: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(18)
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {color}; border-radius: 8px; }} QProgressBar {{ border-radius: 8px; }}"
        )
        layout.addWidget(self.bar)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(900)
        self.tick()

    def tick(self) -> None:
        self.bar.setValue(random.randint(12, 96))


class JarvisQtWindow(QMainWindow):
    append_log = Signal(str, str)
    command_result = Signal(object)
    approvals_result = Signal(object)
    apps_result = Signal(object)
    ai_result = Signal(str)
    voice_result = Signal(bool, bool)
    runtime_ready = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.runtime = None
        self._busy = False
        self._refreshing_apps = False
        self._refreshing_approvals = False
        self._pending_ids: list[str] = []
        self._app_commands: list[str] = []
        self.setWindowTitle("JARVIS Command Center")
        self.resize(1660, 960)
        self.setMinimumSize(1380, 820)

        self.setStyleSheet(self._qss())
        self.append_log.connect(self._append_terminal_line)
        self.command_result.connect(self._handle_command_result)
        self.approvals_result.connect(self._handle_approvals_result)
        self.apps_result.connect(self._handle_apps_result)
        self.ai_result.connect(self._handle_ai_result)
        self.voice_result.connect(self._handle_voice_result)
        self.runtime_ready.connect(self._handle_runtime_ready)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        root_layout.addWidget(self._build_sidebar(), 0)

        main_shell = QWidget()
        main_layout = QVBoxLayout(main_shell)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(14)
        root_layout.addWidget(main_shell, 1)

        main_layout.addWidget(self._build_topbar(), 0)

        body = QWidget()
        body_grid = QGridLayout(body)
        body_grid.setContentsMargins(0, 0, 0, 0)
        body_grid.setHorizontalSpacing(14)
        body_grid.setVerticalSpacing(14)
        body_grid.setColumnStretch(0, 9)
        body_grid.setColumnStretch(1, 4)
        body_grid.setColumnStretch(2, 4)
        main_layout.addWidget(body, 1)

        body_grid.addWidget(self._build_center(), 0, 0)
        body_grid.addWidget(self._build_status_panel(), 0, 1)
        body_grid.addWidget(self._build_right_panel(), 0, 2)

        self._boot_sequence()
        self._clock_timer()
        self._initialize_runtime_async()

    def _card(self, title: str | None = None, cls: str = "card") -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName(cls)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("sectionTitle")
            lay.addWidget(lbl)
        return frame, lay

    def _build_sidebar(self) -> QWidget:
        side, lay = self._card(cls="sidebar")
        side.setFixedWidth(210)
        mark = QLabel("J")
        mark.setObjectName("logoMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(64, 64)
        mark.setGraphicsEffect(neon_shadow(COLORS["cyan"], 30))
        lay.addWidget(mark, 0, Qt.AlignLeft)

        for text, icon in [
            ("Dashboard", "mdi.view-dashboard-outline"),
            ("Voice", "mdi.microphone"),
            ("Apps", "mdi.apps"),
            ("Terminal", "mdi.console"),
            ("Analytics", "mdi.chart-line"),
            ("Settings", "mdi.cog-outline"),
        ]:
            b = GlowButton(text, icon, COLORS["cyan"])
            b.setObjectName("navBtn")
            lay.addWidget(b)

        lay.addStretch(1)
        self.clock_label = QLabel("--:--")
        self.clock_label.setObjectName("clock")
        self.date_label = QLabel("-- --- ----")
        self.date_label.setObjectName("date")
        lay.addWidget(self.clock_label)
        lay.addWidget(self.date_label)
        return side

    def _build_topbar(self) -> QWidget:
        top, lay = self._card(cls="topbar")
        row = QHBoxLayout()
        row.setSpacing(10)
        title = QLabel("JARVIS Command Center")
        title.setObjectName("title")
        row.addWidget(title)
        row.addStretch(1)

        self.voice_btn = GlowButton("Voice Off", "mdi.waveform", COLORS["purple"])
        self.voice_btn.clicked.connect(self._toggle_voice)
        row.addWidget(self.voice_btn)
        refresh_btn = GlowButton("Refresh", "mdi.refresh", COLORS["blue"])
        refresh_btn.clicked.connect(self._refresh_backend_data)
        row.addWidget(refresh_btn)
        clear_btn = GlowButton("Clear Logs", "mdi.broom", COLORS["cyan"])
        clear_btn.clicked.connect(self.terminal_clear)
        row.addWidget(clear_btn)

        status = QWidget()
        srow = QHBoxLayout(status)
        srow.setContentsMargins(12, 6, 12, 6)
        srow.setSpacing(8)
        srow.addWidget(PulseDot())
        self.status_text = QLabel("STATUS: ONLINE")
        srow.addWidget(self.status_text)
        status.setObjectName("statusPill")
        row.addWidget(status)
        lay.addLayout(row)
        return top

    def _build_center(self) -> QWidget:
        center = QWidget()
        lay = QVBoxLayout(center)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        cmd, cmd_lay = self._card("AI Command", "card")
        input_row = QHBoxLayout()
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Type a command for Jarvis...")
        run_btn = GlowButton("Execute", "mdi.send", COLORS["blue"])
        run_btn.clicked.connect(self._run_command)
        self.command_input.returnPressed.connect(self._run_command)
        input_row.addWidget(self.command_input, 1)
        input_row.addWidget(run_btn)
        cmd_lay.addLayout(input_row)
        lay.addWidget(cmd)

        resources, res_lay = self._card("Live Resources", "card")
        charts = QGridLayout()
        charts.setHorizontalSpacing(10)
        charts.setVerticalSpacing(10)
        charts.addWidget(self._chart_card("CPU", COLORS["cyan"]), 0, 0)
        charts.addWidget(self._chart_card("GPU", COLORS["blue"]), 0, 1)
        charts.addWidget(self._chart_card("RAM", COLORS["green"]), 0, 2)
        res_lay.addLayout(charts)
        lay.addWidget(resources)

        terminal, t_lay = self._card("Live Terminal", "card")
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setObjectName("terminal")
        t_lay.addWidget(self.terminal)
        lay.addWidget(terminal, 1)
        return center

    def _chart_card(self, name: str, color: str) -> QWidget:
        w, lay = self._card(cls="miniCard")
        lbl = QLabel(name)
        lbl.setObjectName("miniLabel")
        lay.addWidget(lbl)
        chart = LiveChart(color)
        chart.setMinimumHeight(110)
        lay.addWidget(chart)
        return w

    def _build_status_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        voice, vlay = self._card("Voice Engine", "card")
        wave = WaveformWidget()
        wave.setMinimumHeight(110)
        vlay.addWidget(wave)
        lay.addWidget(voice)

        agent, alay = self._card("Agent Status", "card")
        self.approval_list = QListWidget()
        self.approval_list.addItem("Loading pending approvals...")
        alay.addWidget(self.approval_list)
        approve_btn = GlowButton("Approve Selected", "mdi.check-circle-outline", COLORS["green"])
        approve_btn.clicked.connect(self._approve_selected)
        alay.addWidget(approve_btn)
        lay.addWidget(agent)

        metric, mlay = self._card("System Metrics", "card")
        self.metric_bars: dict[str, QProgressBar] = {}
        for n, v in [("CPU Usage", 0), ("Memory Usage", 0), ("Voice Confidence", 84)]:
            mlay.addWidget(QLabel(n))
            pb = QProgressBar()
            pb.setValue(v)
            self.metric_bars[n] = pb
            mlay.addWidget(pb)
        lay.addWidget(metric)
        lay.addStretch(1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        pipeline, play = self._card("AI Task Queue", "card")
        for txt, col in [
            ("UPCOMING  - Fetching analytics", COLORS["cyan"]),
            ("RUNNING   - Approval routing", COLORS["amber"]),
            ("DONE      - Voice model sync", COLORS["blue"]),
        ]:
            l = QLabel(txt)
            l.setStyleSheet(f"color: {col}; font-weight: 600;")
            play.addWidget(l)
        lay.addWidget(pipeline)

        apps, app_lay = self._card("Available Apps", "card")
        self.app_list = QListWidget()
        self.app_list.addItem("Loading known apps...")
        self.app_list.itemDoubleClicked.connect(self._run_app_item)
        app_lay.addWidget(self.app_list)
        lay.addWidget(apps)

        ai, ailay = self._card("AI Assistant", "card")
        self.ai_output = QPlainTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setPlaceholderText("Assistant responses appear here...")
        self.ai_output.setMinimumHeight(160)
        ailay.addWidget(self.ai_output)
        row = QHBoxLayout()
        self.ai_input = QLineEdit()
        self.ai_input.setPlaceholderText("Ask Jarvis anything...")
        send = GlowButton("Send", "mdi.send", COLORS["blue"])
        send.clicked.connect(self._trigger_ai_response)
        self.ai_input.returnPressed.connect(self._trigger_ai_response)
        row.addWidget(self.ai_input, 1)
        row.addWidget(send)
        ailay.addLayout(row)
        lay.addWidget(ai, 1)
        return panel

    def _trigger_ai_response(self) -> None:
        if self.runtime is None:
            self.ai_output.appendPlainText("JARVIS: Backend still initializing. Please wait.\n")
            return
        prompt = self.ai_input.text().strip()
        if not prompt:
            return
        self.ai_input.clear()
        self.ai_output.appendPlainText(f"You: {prompt}\n")
        self._set_busy(True)
        self._run_in_thread(self._ai_worker, prompt)

    def _ai_worker(self, prompt: str) -> None:
        try:
            history = self.runtime.memory.recent_conversation(limit=12)
            plan = self.runtime.planner.plan(prompt, [], history)
            ans = (plan.final_response or "").strip()
            if not ans:
                ans = self.runtime.orchestrator.execute_command(
                    command=prompt, source="gui_qt_ai", auto_approve=False
                ).response_text
            self.ai_result.emit(ans or "Done.")
        except Exception as exc:
            self.ai_result.emit(f"Error: {exc}")

    def _set_ai_text(self, text: str) -> None:
        lines = self.ai_output.toPlainText().splitlines()
        if lines and lines[-1].startswith("JARVIS:"):
            lines[-1] = text
            self.ai_output.setPlainText("\n".join(lines))
        else:
            self.ai_output.appendPlainText(text)

    def _run_command(self) -> None:
        if self.runtime is None:
            self.append_log.emit("sys", "Runtime is still initializing...")
            return
        if self._busy:
            return
        command = self.command_input.text().strip()
        if not command:
            return
        self.append_log.emit("cmd", f"> {command}")
        self._set_busy(True)
        self._run_in_thread(self._command_worker, command)

    def _command_worker(self, command: str) -> None:
        try:
            result = self.runtime.orchestrator.execute_command(command=command, source="gui_qt", auto_approve=False)
            self.command_result.emit(result.model_dump())
        except Exception as exc:
            self.command_result.emit({"response_text": f"Error: {exc}", "results": []})

    def _handle_command_result(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        self._set_busy(False)
        self.append_log.emit("ok", str(data.get("response_text", "")))
        for item in data.get("results") or []:
            tool = item.get("tool", "tool")
            status = item.get("status", "unknown")
            if status == "completed":
                self.append_log.emit("ok", f"{tool}: completed")
            elif status == "pending":
                self.append_log.emit("warn", f"{tool}: pending approval")
            else:
                self.append_log.emit("err", f"{tool}: {item.get('error', 'failed')}")
        self._refresh_approvals()
        self._refresh_apps()

    def _handle_ai_result(self, text: str) -> None:
        self._set_busy(False)
        self._set_ai_text(f"JARVIS: {text}")
        self.ai_output.appendPlainText("")

    def _toggle_voice(self) -> None:
        if self.runtime is None:
            self.append_log.emit("sys", "Runtime is still initializing...")
            return
        self._set_busy(True)
        self._run_in_thread(self._voice_worker)

    def _voice_worker(self) -> None:
        try:
            if self.runtime.voice.is_running():
                changed = self.runtime.voice.stop()
                self.voice_result.emit(False, bool(changed))
            else:
                changed = self.runtime.voice.start()
                self.voice_result.emit(True, bool(changed))
        except Exception:
            self.voice_result.emit(self.runtime.voice.is_running(), False)

    def _handle_voice_result(self, running: bool, changed: bool) -> None:
        self._set_busy(False)
        if running:
            self.voice_btn.setText("Voice On")
            self.status_text.setText("STATUS: LISTENING")
            self.append_log.emit("sys", "Voice mode enabled.")
        else:
            self.voice_btn.setText("Voice Off")
            self.status_text.setText("STATUS: ONLINE")
            if changed:
                self.append_log.emit("sys", "Voice mode disabled.")

    def _approve_selected(self) -> None:
        row = self.approval_list.currentRow()
        if row < 0 or row >= len(self._pending_ids):
            return
        approval_id = self._pending_ids[row]
        self._set_busy(True)
        self._run_in_thread(self._approve_worker, approval_id)

    def _approve_worker(self, approval_id: str) -> None:
        try:
            result = self.runtime.orchestrator.approve(approval_id)
            self.command_result.emit({"response_text": result.message, "results": []})
        except Exception as exc:
            self.command_result.emit({"response_text": f"Approval failed: {exc}", "results": []})

    def _run_app_item(self, item: QListWidgetItem) -> None:
        idx = self.app_list.row(item)
        if idx < 0 or idx >= len(self._app_commands):
            return
        self.command_input.setText(self._app_commands[idx])
        self._run_command()

    def _refresh_backend_data(self) -> None:
        if self.runtime is None:
            return
        self._refresh_approvals()
        self._refresh_apps()
        self._refresh_voice_state()
        self._refresh_local_metrics()

    def _refresh_approvals(self) -> None:
        if self._refreshing_approvals:
            return
        self._refreshing_approvals = True
        self._run_in_thread(self._approvals_worker)

    def _approvals_worker(self) -> None:
        try:
            items = [i.model_dump() for i in self.runtime.orchestrator.list_pending_approvals()]
            self.approvals_result.emit(items)
        except Exception as exc:
            self.approvals_result.emit([{"approval_id": "", "tool": f"Error: {exc}"}])

    def _handle_approvals_result(self, payload: object) -> None:
        self._refreshing_approvals = False
        self.approval_list.clear()
        self._pending_ids = []
        items = payload if isinstance(payload, list) else []
        if not items:
            self.approval_list.addItem("No pending approvals.")
            return
        for item in items:
            aid = str(item.get("approval_id", ""))
            tool = str(item.get("tool", ""))
            text = f"{aid[:8]}... {tool}" if aid else tool
            self.approval_list.addItem(text)
            if aid:
                self._pending_ids.append(aid)

    def _refresh_apps(self) -> None:
        if self._refreshing_apps:
            return
        self._refreshing_apps = True
        self._run_in_thread(self._apps_worker)

    def _apps_worker(self) -> None:
        try:
            payload = self.runtime.registry.execute("list_known_apps", {})
            self.apps_result.emit(payload)
        except Exception as exc:
            self.apps_result.emit({"error": str(exc)})

    def _handle_apps_result(self, payload: object) -> None:
        self._refreshing_apps = False
        data = payload if isinstance(payload, dict) else {}
        self.app_list.clear()
        self._app_commands = []
        if data.get("error"):
            self.app_list.addItem(f"Error: {data['error']}")
            return
        for item in (data.get("desktop_apps") or [])[:30]:
            alias = str(item.get("alias", "")).strip()
            if not alias:
                continue
            state = "[READY]" if item.get("available") else "[MAY OPEN]"
            self.app_list.addItem(f"{alias} {state}")
            self._app_commands.append(f"open {alias}")
        for item in (data.get("web_apps") or [])[:20]:
            alias = str(item.get("alias", "")).strip()
            if not alias:
                continue
            self.app_list.addItem(f"{alias} [WEB]")
            self._app_commands.append(f"open {alias}")
        if not self._app_commands:
            self.app_list.addItem("No known apps found.")

    def _refresh_voice_state(self) -> None:
        if self.runtime is None:
            self.voice_btn.setText("Voice Off")
            self.status_text.setText("STATUS: INITIALIZING")
            return
        running = self.runtime.voice.is_running()
        self.voice_btn.setText("Voice On" if running else "Voice Off")
        self.status_text.setText("STATUS: LISTENING" if running else "STATUS: ONLINE")

    def _refresh_local_metrics(self) -> None:
        cpu = int(psutil.cpu_percent(interval=None))
        mem = int(psutil.virtual_memory().percent)
        self.metric_bars["CPU Usage"].setValue(cpu)
        self.metric_bars["Memory Usage"].setValue(mem)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.command_input.setEnabled(not busy)
        self.ai_input.setEnabled(not busy)

    def _run_in_thread(self, fn, *args) -> None:
        threading.Thread(target=lambda: fn(*args), daemon=True).start()

    def terminal_clear(self) -> None:
        self.terminal.clear()

    def _initialize_runtime_async(self) -> None:
        self.append_log.emit("sys", "Initializing Jarvis backend...")
        self._run_in_thread(self._runtime_worker)

    def _runtime_worker(self) -> None:
        try:
            self.runtime = build_runtime()
            self.runtime_ready.emit(True, "")
        except Exception as exc:
            self.runtime_ready.emit(False, str(exc))

    def _handle_runtime_ready(self, ok: bool, error: str) -> None:
        if not ok:
            self.status_text.setText("STATUS: INIT FAILED")
            self.append_log.emit("err", f"Runtime init failed: {error}")
            return
        self.status_text.setText("STATUS: ONLINE")
        self.append_log.emit("ok", "Runtime initialized.")
        self._refresh_backend_data()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            if self.runtime is not None:
                self.runtime.voice.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def _append_terminal_line(self, kind: str, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.terminal.appendPlainText(f"[{ts}] {kind.upper():<4} {text}")

    def _clock_timer(self) -> None:
        def refresh() -> None:
            now = datetime.now()
            self.clock_label.setText(now.strftime("%I:%M %p"))
            self.date_label.setText(now.strftime("%d %b %Y"))
            self._refresh_local_metrics()

        refresh()
        timer = QTimer(self)
        timer.timeout.connect(refresh)
        timer.start(1000)
        self._clock_updater = timer

    def _boot_sequence(self) -> None:
        msgs = [
            "Boot sequence initialized.",
            "Neural routing mesh online.",
            "Voice recognition pipeline calibrated.",
            "Command Center ready.",
        ]
        delay = 300
        for msg in msgs:
            QTimer.singleShot(delay, lambda m=msg: self.append_log.emit("sys", m))
            delay += 280

    def _qss(self) -> str:
        return f"""
        * {{
            color: {COLORS['text']};
            font-family: 'Inter', 'Poppins', 'Segoe UI';
            font-size: 14px;
        }}
        QMainWindow {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {COLORS['bg']}, stop:0.5 {COLORS['bg2']}, stop:1 #0b1430);
        }}
        #sidebar {{
            background: rgba(15, 23, 42, 0.88);
            border: 1px solid rgba(56, 189, 248, 0.16);
            border-radius: 24px;
        }}
        #topbar, #card {{
            background: {COLORS['glass']};
            border: 1px solid rgba(56, 189, 248, 0.14);
            border-radius: 22px;
        }}
        #miniCard {{
            background: rgba(15, 23, 42, 0.62);
            border: 1px solid rgba(59, 130, 246, 0.14);
            border-radius: 18px;
        }}
        #sectionTitle {{
            font-family: 'Orbitron', 'Segoe UI';
            font-size: 18px;
            font-weight: 700;
            color: #cbe6ff;
            letter-spacing: 0.5px;
        }}
        #title {{
            font-family: 'Orbitron', 'Segoe UI';
            font-size: 34px;
            font-weight: 700;
            letter-spacing: 0.8px;
        }}
        #logoMark {{
            background: rgba(56, 189, 248, 0.18);
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 32px;
            font-family: 'Orbitron', 'Segoe UI';
            font-size: 28px;
            font-weight: 800;
            color: #e6f6ff;
        }}
        #navBtn {{
            text-align: left;
            padding: 10px 14px;
            border-radius: 16px;
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid rgba(56, 189, 248, 0.08);
            min-height: 40px;
            font-size: 15px;
        }}
        #navBtn:hover {{
            background: rgba(56, 189, 248, 0.18);
            border: 1px solid rgba(56, 189, 248, 0.35);
        }}
        QPushButton {{
            background: rgba(30, 58, 138, 0.55);
            border: 1px solid rgba(56, 189, 248, 0.18);
            border-radius: 14px;
            padding: 10px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(59, 130, 246, 0.78);
            border: 1px solid rgba(56, 189, 248, 0.48);
        }}
        QLineEdit, QListWidget, QPlainTextEdit {{
            background: rgba(2, 6, 23, 0.78);
            border: 1px solid rgba(56, 189, 248, 0.18);
            border-radius: 14px;
            padding: 10px;
            selection-background-color: rgba(56, 189, 248, 0.35);
        }}
        QProgressBar {{
            border: 1px solid rgba(56, 189, 248, 0.16);
            border-radius: 10px;
            background: rgba(2, 6, 23, 0.72);
            text-align: center;
        }}
        QProgressBar::chunk {{
            border-radius: 9px;
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {COLORS['cyan']}, stop:1 {COLORS['blue']});
        }}
        #terminal {{
            font-family: 'Cascadia Code', 'Consolas';
            font-size: 13px;
            border-radius: 16px;
            background: rgba(1, 9, 30, 0.9);
        }}
        #clock {{ font-size: 24px; font-weight: 700; }}
        #date {{ color: {COLORS['muted']}; font-size: 13px; }}
        #miniLabel {{ color: {COLORS['muted']}; font-size: 13px; font-weight: 600; }}
        #statusPill {{
            background: rgba(2, 20, 40, 0.85);
            border: 1px solid rgba(56, 189, 248, 0.28);
            border-radius: 14px;
        }}
        """


def main() -> None:
    app = QApplication(sys.argv)
    win = JarvisQtWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
