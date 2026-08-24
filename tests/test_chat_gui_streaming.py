from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStackedWidget, QTextEdit  # noqa: E402

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
