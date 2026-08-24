from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
)

from app.gui import JarvisHudApp, markdown_to_html  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture()
def hud(qapp: QApplication) -> Any:
    hud = JarvisHudApp.__new__(JarvisHudApp)  # bypass Qt/runtime construction
    hud.chat_text = QTextEdit()
    hud.chat_text.setObjectName("ChatFlow")
    stack = QStackedWidget()
    stack.addWidget(QTextEdit())
    body = QTextEdit()
    stack.addWidget(body)
    hud.ai_stack = stack
    stack.setCurrentIndex(1)
    hud.ai_state_chip = QLabel("● READY")
    hud.ask_button = QPushButton("ASK")
    hud.ask_button.setEnabled(True)
    hud.run_button = QPushButton("EXECUTE")
    hud.chat_status_label = QLabel("")
    hud.chat_entry = QLineEdit()
    hud.chat_image_button = QPushButton("IMAGE")
    hud._chat_streaming = False
    hud._chat_responding = False
    hud._chat_stream_start_offset = -1
    hud._chat_stream_text = []
    hud._chat_cancel = None
    hud._busy = False
    return hud


def test_first_token_updates_gui_incrementally(hud: Any) -> None:
    hud._start_chat_stream()
    hud._append_chat_stream_chunk("Hel")
    assert "Hel" in hud.chat_text.toPlainText()
    assert hud.ai_state_chip.text().endswith("RESPONDING")

    before = len(hud.chat_text.toPlainText())
    hud._append_chat_stream_chunk("lo there")
    after = len(hud.chat_text.toPlainText())
    assert after > before, "GUI text must grow per streamed token batch"
    assert hud._chat_streaming is True


def test_finish_replaces_plain_with_markdown(hud: Any) -> None:
    hud._start_chat_stream()
    for chunk in ("**Bold** ", "and ", "`code`"):
        hud._append_chat_stream_chunk(chunk)

    full = "".join(hud._chat_stream_text)
    hud._finish_chat_stream_text(full, False)

    html = hud.chat_text.toHtml()
    assert "<b>" in html or "bold</span>" in html or "Bold" in html
    assert "Bold" in hud.chat_text.toPlainText()
    assert hud._chat_streaming is False
    assert hud.ask_button.text() == "ASK"
    assert hud.ask_button.isEnabled()
    assert hud.ai_state_chip.text().endswith("READY")


def test_stopped_marker_rendered(hud: Any) -> None:
    hud._start_chat_stream()
    hud._append_chat_stream_chunk("partial answer")
    hud._finish_chat_stream_text("partial answer", True)
    assert "stopped" in hud.chat_text.toPlainText()


def test_failed_partial_replaced_by_fallback_response(hud: Any) -> None:
    """B1 regression: failed attempt's partial text must be discarded before the fallback renders."""
    hud._start_chat_stream()
    hud._append_chat_stream_chunk("Hello ")
    hud._append_chat_stream_chunk("partial")
    assert "partial" in hud.chat_text.toPlainText()

    JarvisHudApp._reset_chat_stream_region(hud)
    assert hud._chat_stream_text == []
    assert "partial" not in hud.chat_text.toPlainText()

    hud._append_chat_stream_chunk("Hello ")
    hud._append_chat_stream_chunk("world")
    full = "".join(hud._chat_stream_text)
    assert full == "Hello world"
    hud._finish_chat_stream_text(full, False)

    plain = hud.chat_text.toPlainText()
    assert "Hello world" in plain
    assert "partial" not in plain
    assert "Hello Hello" not in plain
    assert hud._chat_stream_start_offset == -1
    assert hud.ask_button.text() == "ASK"


def test_clear_resets_conversation_state(hud: Any) -> None:
    import threading

    hud._start_chat_stream()
    hud._append_chat_stream_chunk("temp")
    hud._chat_cancel = threading.Event()

    JarvisHudApp._clear_conversation(hud)

    assert hud.chat_text.toPlainText() == ""
    assert hud.ai_stack.currentIndex() == 0, "idle panel must return after clear"
    assert hud._chat_stream_start_offset == -1
    assert hud.ask_button.text() == "ASK"


# ----------------------------------------------------------------- B4: no silent input loss


class ToasterSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(self, title: str, message: str, color: object = "", duration_ms: int = 3000) -> None:
        self.calls.append((title, message))


def wire_submission(hud: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub everything _ask_chatbot touches after acceptance; return observation spies."""
    state: dict[str, Any] = {
        "service_calls": 0,
        "terminal": [],
        "logged": [],
        "started": threading.Event(),
        "release": threading.Event(),
    }

    def stream_chat(_text: str, _history: list[Any], _cancel: Any) -> Any:
        state["service_calls"] += 1
        state["started"].set()
        state["release"].wait(timeout=2.0)
        if False:  # pragma: no cover - generator formalities
            yield  # type: ignore[unreachable]

    hud._toaster = ToasterSpy()
    hud._append_terminal = lambda tag, msg: state["terminal"].append((tag, msg))
    hud._pending_images = []
    hud._last_user_text = ""
    hud.runtime = SimpleNamespace(
        settings=SimpleNamespace(jarvis_chat_history_window=12),
        memory=SimpleNamespace(log_command=lambda **kw: state["logged"].append(kw)),
    )
    hud._chat_service = SimpleNamespace(stream_chat=stream_chat)
    monkeypatch.setattr("app.gui.build_history_from_memory", lambda memory, window: [])
    return state


def test_busy_command_execution_preserves_typed_message(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = wire_submission(hud, monkeypatch)
    hud._busy = True  # a command is currently executing
    hud.chat_entry.setText("remember the launch plan")

    JarvisHudApp._on_ask_clicked(hud)

    assert hud.chat_entry.text() == "remember the launch plan", "typed text must never be lost"
    assert state["service_calls"] == 0
    assert len(hud._toaster.calls) == 1
    assert "busy" in hud._toaster.calls[0][0].lower()
    assert any("executing a command" in msg for _title, msg in hud._toaster.calls)
    assert state["terminal"], "user must receive non-blocking feedback"
    assert hud._chat_streaming is False


def test_streaming_busy_rejects_new_submission_without_loss(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = wire_submission(hud, monkeypatch)
    hud.chat_entry.setText("next question")
    hud._chat_streaming = True

    accepted = hud._ask_chatbot("next question")

    assert accepted is False
    assert hud.chat_entry.text() == "next question"
    assert state["service_calls"] == 0
    assert any("still responding" in msg for _title, msg in hud._toaster.calls)


def test_accepted_submission_clears_input_and_starts_stream(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = wire_submission(hud, monkeypatch)
    hud.chat_entry.setText("what is a neutron star")

    JarvisHudApp._on_ask_clicked(hud)
    assert state["started"].wait(timeout=2.0), "accepted submission must start the chat worker"

    assert hud.chat_entry.text() == "", "accepted submission must clear the composer normally"
    assert state["service_calls"] == 1
    assert hud._busy is True
    assert hud._chat_streaming is True
    assert hud.ask_button.text() == "STOP"
    assert "neutron star" in hud.chat_text.toPlainText()

    state["release"].set()


def test_busy_state_cleared_correctly_after_operation_finishes(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = wire_submission(hud, monkeypatch)
    hud.chat_entry.setText("what is a neutron star")
    JarvisHudApp._on_ask_clicked(hud)
    assert state["started"].wait(timeout=2.0)
    state["release"].set()

    # The poller dispatches chat_stream_done to this handler on the GUI thread.
    hud._handle_chat_done({"text": "A neutron star.", "stopped": False, "meta": {}})

    assert hud._busy is False
    assert hud._chat_streaming is False
    assert hud.ask_button.text() == "ASK"
    assert hud.ask_button.isEnabled()
    assert hud.run_button.isEnabled()
    assert state["logged"] and state["logged"][0]["response_text"] == "A neutron star."


def test_retry_submission_keeps_composer_draft(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = wire_submission(hud, monkeypatch)
    hud.chat_entry.setText("draft in progress")
    hud._last_user_text = "earlier question"

    accepted = hud._ask_chatbot(hud._last_user_text)
    assert state["started"].wait(timeout=2.0)

    assert accepted is True
    assert hud.chat_entry.text() == "draft in progress", "RETRY must not consume an unrelated draft"
    state["release"].set()


def test_enter_and_send_button_follow_same_path(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return-pressed and ASK-clicked share one handler, so their behavior is identical."""
    state = wire_submission(hud, monkeypatch)
    hud._busy = True
    hud.chat_entry.setText("queued while busy")

    JarvisHudApp._on_ask_clicked(hud)  # keyboard: returnPressed wiring
    first_toasts = len(hud._toaster.calls)
    assert first_toasts == 1

    JarvisHudApp._on_ask_clicked(hud)  # send button: clicked wiring

    assert hud.chat_entry.text() == "queued while busy"
    assert len(hud._toaster.calls) == 2
    assert state["service_calls"] == 0


def test_empty_input_is_ignored_without_noise(hud: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = wire_submission(hud, monkeypatch)

    JarvisHudApp._on_ask_clicked(hud)

    assert state["service_calls"] == 0
    assert not hud._toaster.calls


def test_stop_during_stream_preserves_composer_text(hud: Any) -> None:
    """Existing ASK->STOP behavior stays intact and never consumes the composer."""
    hud._chat_streaming = True
    hud.chat_entry.setText("type ahead while thinking")
    hud.ask_button.setText("STOP")

    JarvisHudApp._on_ask_clicked(hud)

    assert hud.chat_entry.text() == "type ahead while thinking"
    assert hud.ask_button.text() == "…"


# --------------------------------------------------------------------------- markdown


def test_markdown_renders_expected_subset() -> None:
    md = (
        "# Title\n\n"
        "This is **bold** and *italic*.\n\n"
        "- item one\n- item two\n\n"
        "```python\nx = 1 < 2\n```\n\n"
        "See [docs](https://example.com)."
    )
    html = markdown_to_html(md)
    assert "&lt;" in html, "raw HTML must be escaped before rendering"
    assert "<b>bold</b>" in html
    assert "<i>italic</i>" in html
    assert "•" in html
    assert 'href="https://example.com"' in html
    assert "bgcolor=" in html, "fenced code block should render as an inset table"
    assert "Title" in html


def test_markdown_empty_is_empty() -> None:
    assert markdown_to_html("") == ""
    assert markdown_to_html("   \n ") == ""
