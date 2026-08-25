"""Tests for BrowserSession lifecycle hardening.

Covers: normal startup, context reuse, dead-context recovery, profile
fallback, cleanup after failure, and thread safety.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.tools.browser_session import (
    BrowserSession,
    _is_browser_missing_error,
    _is_profile_error,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self._closed = False

    def goto(self, *, wait_until: str = "load", timeout: int = 0) -> None:
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")

    def bring_to_front(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True


class _FakeContext:
    def __init__(self, pages: list[_FakePage] | None = None) -> None:
        self._pages: list[_FakePage] = pages or []
        self._closed = False

    @property
    def pages(self) -> list[_FakePage]:
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return self._pages

    def new_page(self) -> _FakePage:
        if self._closed:
            raise RuntimeError("Browser has been closed")
        p = _FakePage()
        self._pages.append(p)
        return p

    def close(self) -> None:
        self._closed = True


def _make_session(tmp_path: Path) -> BrowserSession:
    """Build a BrowserSession with mocked settings (no real Playwright)."""
    from app.config import Settings
    settings = MagicMock(spec=Settings)
    settings.browser_automation_timeout_ms = 5000
    settings.browser_profile_dir = str(tmp_path / "profile")
    return BrowserSession(settings)


# ---------------------------------------------------------------------------
# error classifiers
# ---------------------------------------------------------------------------

class TestErrorClassifiers:
    def test_profile_error_detection(self) -> None:
        assert _is_profile_error(RuntimeError("Target closed"))
        assert _is_profile_error(RuntimeError("context or browser has been closed"))
        assert _is_profile_error(RuntimeError("profile is used by another browser"))
        assert not _is_profile_error(RuntimeError("connection refused"))

    def test_browser_missing_detection(self) -> None:
        assert _is_browser_missing_error(RuntimeError("browser executable not found"))
        assert _is_browser_missing_error(RuntimeError("Playwright browser executable is missing"))
        assert not _is_browser_missing_error(RuntimeError("Target closed"))


# ---------------------------------------------------------------------------
# normal startup + reuse
# ---------------------------------------------------------------------------

class TestNormalStartup:
    def test_ensure_context_returns_same_instance(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        ctx = _FakeContext()
        session._context = ctx
        result = session.ensure_context()
        assert result is ctx

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        session.close()
        session.close()  # must not raise


# ---------------------------------------------------------------------------
# dead context recovery
# ---------------------------------------------------------------------------

class TestDeadContextRecovery:
    def test_ensure_context_resets_on_dead_probe(self, tmp_path: Path) -> None:
        """When _context exists but is dead (probe fails), ensure_context
        resets and calls _create_context_locked to build a new one."""
        session = _make_session(tmp_path)
        dead_ctx = _FakeContext()
        dead_ctx._closed = True
        session._context = dead_ctx
        session._playwright = MagicMock()

        # Verify initial state
        assert session._context is dead_ctx

        # After calling ensure_context with a dead context, _context should
        # have been replaced (reset + _create_context_locked called).
        # Since _create_context_locked will try real Playwright (which we
        # can't easily mock at module-import time), we verify the reset
        # happened by checking the old context was discarded.
        #
        # Instead, verify that the probe-failure path is entered:
        with pytest.raises((RuntimeError, AttributeError)):
            # _create_context_locked will fail because Playwright is not installed,
            # but the important thing is that the dead context was detected and
            # _create_context_locked WAS called (not just returned dead_ctx).
            session.ensure_context()

        # The dead context should have been discarded
        assert session._context is None or session._context is not dead_ctx

    def test_ensure_context_with_valid_context_skips_reset(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        ctx = _FakeContext()
        session._context = ctx
        result = session.ensure_context()
        assert result is ctx


# ---------------------------------------------------------------------------
# fallback profile
# ---------------------------------------------------------------------------

class TestFallbackProfile:
    def test_fallback_dir_created(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        fallback = session._get_fallback_dir()
        assert fallback.exists()
        assert "jarvis_browser_fallback" in str(fallback)

    def test_fallback_dir_reused(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        d1 = session._get_fallback_dir()
        d2 = session._get_fallback_dir()
        assert d1 == d2


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_close_resets_state(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        session._context = _FakeContext()
        session._playwright = MagicMock()
        session.close()
        assert session._context is None
        assert session._playwright is None

    def test_reset_clears_state(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        session._context = _FakeContext()
        session._playwright = MagicMock()
        session._reset()
        assert session._context is None
        assert session._playwright is None


# ---------------------------------------------------------------------------
# ensure_page recovery
# ---------------------------------------------------------------------------

class TestEnsurePageRecovery:
    def test_ensure_page_resets_and_retries(self, tmp_path: Path) -> None:
        """ensure_page should recover from a dead context by resetting."""
        session = _make_session(tmp_path)
        # Set a valid context and page
        ctx = _FakeContext()
        session._context = ctx

        # On first attempt, find_page returns a dead page (goto fails)
        dead_page = _FakePage()
        dead_page._closed = True
        ctx._pages = [dead_page]

        # After reset, _create_context_locked will be called.
        # It will fail because Playwright is not installed, which is fine —
        # the key assertion is that the first attempt DID fail and DID reset.
        with pytest.raises(RuntimeError, match="Failed to open page after recovery"):
            session.ensure_page("https://example.com", ["example"])

        # The old context should have been discarded
        assert session._context is None


# ---------------------------------------------------------------------------
# thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_close_is_reentrant(self, tmp_path: Path) -> None:
        """Multiple threads calling close() simultaneously must not deadlock."""
        session = _make_session(tmp_path)
        session._context = _FakeContext()
        session._playwright = MagicMock()

        errors: list[Exception] = []

        def closer() -> None:
            try:
                session.close()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=closer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"close() deadlocked or raised: {errors}"
        assert session._context is None


# ---------------------------------------------------------------------------
# integration: BrowserSession + Job Agent isolation
# ---------------------------------------------------------------------------

class TestJobAgentIsolation:
    def test_search_site_failure_does_not_crash_run(self, tmp_path: Path) -> None:
        """Browser failure in _search_site should skip the site, not crash."""
        from app.agents.job_agent import JobAgent
        from app.core.memory import MemoryStore

        settings = MagicMock()
        settings.job_profile_path = str(tmp_path / "profile.yaml")
        settings.job_site_profiles_path = str(tmp_path / "sites.yaml")
        settings.resume_pdf_path = str(tmp_path / "resume.pdf")
        settings.job_target_sites = ["test_site"]
        settings.job_max_apply_per_run = 5
        settings.job_apply_remote_only = False

        memory = MagicMock(spec=MemoryStore)
        memory.get_state.return_value = {}
        memory.recent_job_applications.return_value = []

        session = MagicMock()
        session.ensure_page.side_effect = RuntimeError("Target closed")

        agent = JobAgent(settings, memory, session)
        agent._site_profiles = {"test_site": {"name": "test", "base_url": "https://test.com", "search_path": "/q", "search_params": {"q": "{query}"}}}

        task = {
            "task_id": "test",
            "query": "python dev",
            "phase": "starting",
            "status": "running",
            "applied": 0,
            "skipped": 0,
            "failed": 0,
            "sites": ["test_site"],
            "max_applications": 5,
            "progress": [],
            "active_job": None,
            "paused_reason": None,
        }
        agent._update = lambda t, **kw: t.update(kw)

        agent._run("python dev", task)
        assert any("browser error" in p for p in task.get("progress", []))

    def test_all_sites_browser_error_completes_gracefully(self, tmp_path: Path) -> None:
        """If every site fails with browser errors, the run completes (not crashes)."""
        from app.agents.job_agent import JobAgent
        from app.core.memory import MemoryStore

        settings = MagicMock()
        settings.job_profile_path = str(tmp_path / "profile.yaml")
        settings.job_site_profiles_path = str(tmp_path / "sites.yaml")
        settings.resume_pdf_path = str(tmp_path / "resume.pdf")
        settings.job_target_sites = ["site_a", "site_b"]
        settings.job_max_apply_per_run = 5
        settings.job_apply_remote_only = False

        memory = MagicMock(spec=MemoryStore)
        memory.get_state.return_value = {}
        memory.recent_job_applications.return_value = []

        session = MagicMock()
        session.ensure_page.side_effect = RuntimeError("Browser closed")

        agent = JobAgent(settings, memory, session)
        agent._site_profiles = {
            "site_a": {"name": "a", "base_url": "https://a.com", "search_path": "/q", "search_params": {"q": "{query}"}},
            "site_b": {"name": "b", "base_url": "https://b.com", "search_path": "/q", "search_params": {"q": "{query}"}},
        }

        task = {
            "task_id": "test",
            "query": "dev",
            "phase": "starting",
            "status": "running",
            "applied": 0,
            "skipped": 0,
            "failed": 0,
            "sites": ["site_a", "site_b"],
            "max_applications": 5,
            "progress": [],
            "active_job": None,
            "paused_reason": None,
        }
        agent._update = lambda t, **kw: t.update(kw)

        agent._run("dev", task)
        # Run completed (did not crash), with browser errors logged
        assert any("browser error" in p for p in task.get("progress", []))
        assert task["status"] != "failed" or "browser" in (task.get("paused_reason") or "")
