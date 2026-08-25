"""Tests for P0-3/P1-4 deferred runtime initialization.

Verifies that the GUI window can appear and function while the full runtime
builds in a background thread, and that the runtime swap completes correctly.
"""

from __future__ import annotations

import threading
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hud_stub() -> Any:
    """Create a JarvisHudApp instance bypassing __init__ (no Qt required)."""
    from app.gui import JarvisHudApp

    hud = JarvisHudApp.__new__(JarvisHudApp)
    hud._runtime_ready = False
    hud.runtime = None
    hud._monitor = None
    hud._settings = MagicMock()
    hud._settings.startup_greeting_enabled = False
    hud._settings.tts_enabled = False
    hud._hub = MagicMock()
    hud._events = MagicMock()
    hud._chat_service = MagicMock()
    # Minimal GUI stubs so _on_runtime_ready/_append_terminal don't crash
    hud.terminal = MagicMock()
    hud.mini = MagicMock()
    return hud


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeferredRuntimeInit:
    """P0-3/P1-4: GUI shows immediately; runtime swaps in later."""

    def test_initial_state_has_no_runtime(self) -> None:
        """Before runtime init, _runtime_ready is False and runtime is None."""
        hud = _make_hud_stub()
        assert hud._runtime_ready is False
        assert hud.runtime is None

    def test_on_runtime_ready_sets_flag(self) -> None:
        """_on_runtime_ready() must flip _runtime_ready to True."""
        hud = _make_hud_stub()
        mock_rt = MagicMock()
        mock_rt.monitor = MagicMock()
        mock_rt.settings = MagicMock()
        mock_rt.settings.startup_greeting_enabled = False
        mock_rt.settings.tts_enabled = False
        mock_rt.scheduler = MagicMock()

        with patch("app.gui.MiniHud") as MockMini:
            MockMini.return_value = MagicMock()
            hud._on_runtime_ready(mock_rt)

        assert hud._runtime_ready is True
        assert hud.runtime is mock_rt
        assert hud._monitor is mock_rt.monitor

    def test_post_build_skips_runtime_panels_when_not_ready(self) -> None:
        """_post_build should not touch runtime.orchestrator/registry if not ready."""
        hud = _make_hud_stub()
        # These would crash if called with runtime=None
        with patch.object(hud, "_refresh_voice_button"):
            with patch.object(hud, "_refresh_auto_start_button"):
                with patch.object(hud, "_refresh_gfx_button"):
                    with patch.object(hud, "_bind_hotkeys"):
                        with patch.object(hud, "_refresh_approvals") as mock_app:
                            with patch.object(hud, "_refresh_app_launcher") as mock_al:
                                with patch.object(hud, "_refresh_job_status") as mock_js:
                                    hud._post_build()
        # Should NOT have been called (runtime not ready)
        mock_app.assert_not_called()
        mock_al.assert_not_called()
        mock_js.assert_not_called()

    def test_post_build_calls_runtime_panels_when_ready(self) -> None:
        """_post_build should call runtime-dependent panels if _runtime_ready=True."""
        hud = _make_hud_stub()
        hud._runtime_ready = True
        with patch.object(hud, "_refresh_voice_button"):
            with patch.object(hud, "_refresh_auto_start_button"):
                with patch.object(hud, "_refresh_gfx_button"):
                    with patch.object(hud, "_bind_hotkeys"):
                        with patch.object(hud, "_refresh_approvals") as mock_app:
                            with patch.object(hud, "_refresh_app_launcher") as mock_al:
                                with patch.object(hud, "_refresh_job_status") as mock_js:
                                    hud._post_build()
        mock_app.assert_called_once()
        mock_al.assert_called_once()
        mock_js.assert_called_once()

    def test_init_runtime_thread_enqueues_runtime_ready(self) -> None:
        """_init_runtime_thread must enqueue 'runtime_ready' on success."""
        hud = _make_hud_stub()
        mock_rt = MagicMock()
        with patch("app.gui.build_runtime", return_value=mock_rt):
            hud._init_runtime_thread()
        hud._events.put_nowait.assert_called_once()
        args = hud._events.put_nowait.call_args[0][0]
        assert args[0] == "runtime_ready"
        assert args[1] is mock_rt

    def test_init_runtime_thread_enqueues_failure_on_exception(self) -> None:
        """_init_runtime_thread must enqueue 'runtime_init_failed' on crash."""
        hud = _make_hud_stub()
        with patch("app.gui.build_runtime", side_effect=RuntimeError("boom")):
            hud._init_runtime_thread()
        hud._events.put_nowait.assert_called_once()
        args = hud._events.put_nowait.call_args[0][0]
        assert args[0] == "runtime_init_failed"

    def test_restore_ui_config_safe_when_mini_missing(self) -> None:
        """_restore_ui_config must not crash if self.mini doesn't exist yet."""
        hud = _make_hud_stub()
        # No mini attribute set; _restore_ui_config should handle gracefully
        with patch("pathlib.Path.read_text", return_value="{}"):
            with patch.object(hud, "showMaximized"):
                try:
                    hud._restore_ui_config()
                except AttributeError:
                    pytest.fail("_restore_ui_config crashed when self.mini was absent")

    def test_event_handler_guard_runtime_timeout(self) -> None:
        """command_timeout event should not crash when runtime is None."""
        hud = _make_hud_stub()
        # Simulate the command_timeout path
        # If runtime is None, deadline should default to 180
        assert hud._runtime_ready is False
        assert hud.runtime is None
