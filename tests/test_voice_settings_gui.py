"""B6 regression tests: voice-settings work runs off the Qt GUI thread."""

from __future__ import annotations

import os
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QRadioButton,
)

from app.voice.speaker import Speaker  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


class ToasterSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(self, title: str, message: str, color: object = "", duration_ms: int = 3000) -> None:
        self.calls.append((title, message))


@pytest.fixture()
def rig(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.gui import JarvisHudApp

    install_fake_sapi(monkeypatch)  # every rig speaks to the fake SAPI only
    speaker = Speaker()
    speaker._refresh_audio_outputs()  # warm cache: status() never schedules real COM work
    state: dict[str, Any] = {
        "queued": [],
        "terminal": [],
    }
    hud = JarvisHudApp.__new__(JarvisHudApp)
    hud._enqueue = lambda item: state["queued"].append(item)
    hud._toaster = ToasterSpy()  # type: ignore[assignment]
    hud._append_terminal = lambda kind, text: state["terminal"].append((kind, text))
    hud._voice_settings_generation = 0
    hud._voice_apply_lock = threading.Lock()
    hud._voice_refs = {}
    # Mirror VoiceController's thin delegation (loop.py) without its heavy wiring.
    hud.runtime = SimpleNamespace(  # type: ignore[assignment]
        voice=SimpleNamespace(
            speaker=speaker,
            set_speaker_mode=speaker.set_mode,
            set_output_device=speaker.set_output_index,
            list_output_devices=lambda: {
                "count": speaker.status().get("audio_output_count", 0),
                "devices": speaker.status().get("audio_outputs", []),
                "selected_index": speaker.status().get("preferred_output_index"),
                "mode": speaker.status().get("mode"),
            },
        )
    )

    def pump() -> None:
        while state["queued"]:
            topic, payload = state["queued"].pop(0)
            assert topic == "voice_settings_result"
            hud._handle_voice_settings_result(dict(payload))

    rig_obj = SimpleNamespace(hud=hud, speaker=speaker, state=state, pump=pump, monkeypatch=monkeypatch)
    return rig_obj


def wait_until(predicate: Any, timeout_s: float = 10.0) -> bool:
    """Bounded wait for a worker-visible state change (no fixed sleeps)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return predicate()


def drain_when(rig: Any, timeout_s: float = 10.0) -> None:
    """Bounded wait for the worker''s completion event, then dispatch it like _poll_events."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not rig.state["queued"]:
        threading.Event().wait(0.01)
    rig.pump()


def install_fake_kokoro(monkeypatch: pytest.MonkeyPatch, gate: threading.Event | None = None, fail: bool = False):
    """Inject a fake kokoro module whose KPipeline construction is observable/gated."""
    constructed: list[Any] = []

    class FakePipeline:
        def __init__(self, lang_code: str, repo_id: str = "hexgrad/Kokoro-82M", **kwargs: Any) -> None:
            if gate is not None:
                assert gate.wait(timeout=2.0), "worker never ran"
            if fail:
                raise RuntimeError("model files missing")
            self.lang_code = lang_code
            self.repo_id = repo_id
            constructed.append(self)

    module = ModuleType("kokoro")
    module.KPipeline = FakePipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kokoro", module)
    return constructed


def install_fake_sapi(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOutputItem:
        def __init__(self, i: int) -> None:
            self.i = i

        def GetDescription(self) -> str:
            return f"Speakers {self.i}"

    class FakeOutputs:
        Count = 2

        def Item(self, i: int) -> FakeOutputItem:
            return FakeOutputItem(i)

    class FakeSpVoice:
        def GetAudioOutputs(self) -> FakeOutputs:
            return FakeOutputs()

    client = SimpleNamespace(Dispatch=lambda _name: FakeSpVoice())
    # ``import win32com.client`` binds the top-level package then reads the
    # attribute, so the fake must be present under both sys.modules keys.
    package = ModuleType("win32com")
    package.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", client)


# --------------------------------------------------------------------------- workers


def test_speaker_mode_change_defers_kokoro_init_off_calling_thread(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    constructed = install_fake_kokoro(monkeypatch, gate=gate)
    hud, speaker = rig.hud, rig.speaker

    hud._apply_speaker_mode("kokoro")

    # The expensive KPipeline construction has NOT happened on the calling
    # (GUI-equivalent) thread; it is parked inside the worker until released.
    assert constructed == [], "Kokoro init must not run synchronously on the GUI thread"

    gate.set()
    assert wait_until(lambda: len(constructed) == 1)
    rig.pump()

    assert speaker.status()["mode"] == "kokoro"


def test_lang_change_reinit_runs_in_worker_path(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    constructed = install_fake_kokoro(monkeypatch, gate=gate)
    hud, speaker = rig.hud, rig.speaker
    sentinel = object()
    speaker._kokoro_pipeline = sentinel  # type: ignore[assignment]

    hud._apply_kokoro_reinit()
    assert speaker._kokoro_pipeline is sentinel, "old pipeline must remain until worker finishes"

    gate.set()
    assert wait_until(lambda: bool(constructed))
    rig.pump()

    assert speaker._kokoro_pipeline is not sentinel


def test_output_device_change_enumerates_in_worker(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    hud, speaker = rig.hud, rig.speaker

    original_refresh = speaker._refresh_audio_outputs

    def gated_refresh() -> None:
        gate.wait(timeout=2.0)
        original_refresh()

    monkeypatch.setattr(speaker, "_refresh_audio_outputs", gated_refresh)

    hud._apply_output_device(1)
    assert speaker._preferred_output_index is None, "device apply must be deferred to the worker"

    gate.set()
    assert wait_until(lambda: rig.speaker._preferred_output_index == 1)
    drain_when(rig)

    devices = rig.hud.runtime.voice.list_output_devices()
    assert devices["count"] == 2, "worker performed the SAPI COM enumeration"


def test_dialog_open_refresh_populates_combo_on_result(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    hud = rig.hud
    combo = QComboBox()
    dialog = QDialog()
    hud._voice_refs = {"dialog": dialog, "out_combo": combo}

    hud._refresh_output_devices_async()
    assert wait_until(lambda: bool(rig.state["queued"]))
    rig.pump()
    assert combo.count() == 2
    assert combo.itemText(0) == "Speakers 0"


# --------------------------------------------------------------------------- GUI results


def test_successful_mode_result_syncs_engine_radios(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_kokoro(monkeypatch)  # instant success
    hud = rig.hud
    dialog = QDialog()
    # Shared parent => Qt auto-exclusive radio group, like the real dialog.
    buttons = {name: QRadioButton(name.upper(), dialog) for name in ("kokoro", "sapi", "pyttsx3")}
    buttons["sapi"].setChecked(True)
    hud._voice_refs = {"dialog": dialog, "engine_buttons": buttons}

    hud._apply_speaker_mode("kokoro")
    drain_when(rig)

    assert buttons["kokoro"].isChecked()
    assert not buttons["sapi"].isChecked()


def test_worker_failure_reports_error_and_preserves_working_config(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_kokoro(monkeypatch, fail=True)
    hud, speaker = rig.hud, rig.speaker
    dialog = QDialog()
    buttons = {name: QRadioButton(name.upper(), dialog) for name in ("kokoro", "sapi", "pyttsx3")}
    buttons["kokoro"].setChecked(True)
    hud._voice_refs = {"dialog": dialog, "engine_buttons": buttons}
    previous_mode = speaker.status()["mode"]

    hud._apply_speaker_mode("kokoro")
    drain_when(rig)

    assert any(
        "failed" in f"{title} {msg}".lower() or "unavailable" in f"{title} {msg}".lower()
        for title, msg in hud._toaster.calls
    )
    assert rig.state["terminal"], "failure must surface through existing feedback channels"
    assert speaker.status()["mode"] == previous_mode, "previous working configuration stays intact"
    assert buttons[previous_mode].isChecked(), "radio falls back to the working engine"


def test_stale_completion_cannot_overwrite_newer_selection(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    hud, speaker = rig.hud, rig.speaker
    combo = QComboBox()
    dialog = QDialog()
    hud._voice_refs = {"dialog": dialog, "out_combo": combo}

    slow_gate = threading.Event()
    original_refresh = speaker._refresh_audio_outputs

    def slow_refresh() -> None:
        slow_gate.wait(timeout=2.0)
        original_refresh()

    monkeypatch.setattr(speaker, "_refresh_audio_outputs", slow_refresh)
    hud._apply_output_device(0)  # token 1: parked in the worker

    hud._apply_output_device(1)  # token 2: newer selection wins immediately

    slow_gate.set()
    assert wait_until(lambda: len(rig.state["queued"]) >= 2)
    rig.pump()  # delivers token 1 (stale -> dropped) then token 2 (syncs UI)

    # Worker-level protection: token 1 was superseded under the apply lock, so
    # it can never re-apply device 0 after the user picked device 1.
    assert speaker._preferred_output_index == 1
    assert combo.currentData() == 1


def test_superseded_worker_never_applies_or_enqueues(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    hud, speaker = rig.hud, rig.speaker
    gate = threading.Event()
    original_refresh = speaker._refresh_audio_outputs

    def gated_refresh() -> None:
        gate.wait(timeout=2.0)
        original_refresh()

    monkeypatch.setattr(speaker, "_refresh_audio_outputs", gated_refresh)

    hud._apply_output_device(0)  # token 1, blocked pre-apply
    hud._apply_output_device(1)  # token 2 supersedes it
    gate.set()
    drain_when(rig)

    tokens = [payload.get("token") for _topic, payload in rig.state["queued"]]
    assert 1 not in tokens, "superseded worker must be skipped entirely"
    rig.pump()
    assert speaker._preferred_output_index == 1


def test_results_after_dialog_close_are_safe(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    hud = rig.hud
    hud._voice_refs = {}  # dialog closed / cleared

    hud._apply_output_device(1)
    drain_when(rig)

    assert rig.speaker._preferred_output_index == 1


def test_voice_workers_are_daemon_threads(rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    speaker = rig.speaker
    original_refresh = speaker._refresh_audio_outputs

    def gated_refresh() -> None:
        gate.wait(timeout=2.0)
        original_refresh()

    monkeypatch.setattr(speaker, "_refresh_audio_outputs", gated_refresh)

    before = {t.name for t in threading.enumerate()}
    rig.hud._apply_output_device(0)
    worker_threads = [
        t for t in threading.enumerate() if t.name.startswith("jarvis-voice-") and t.name not in before
    ]
    assert worker_threads, "a dedicated worker thread should exist while work is pending"
    assert all(t.daemon for t in worker_threads), "shutdown must never block on voice workers"

    gate.set()
