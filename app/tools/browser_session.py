from __future__ import annotations

import atexit
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.utils import safe_int

logger = get_logger("browser_session")

# Errors that indicate the persistent profile is unusable (locked, corrupted,
# or another Chromium instance owns it).  On these we retry with a fallback
# temp profile rather than propagating a hard failure.
_PROFILE_RELATED_ERRORS = (
    "target closed",
    "target page",
    "context or browser has been closed",
    "browser has been closed",
    "session crashed",
    "profile is used by another browser",
    "could not lock",
    "unable to open",
    "corrupt",
    "valid browser context has not been created",
)


def _is_profile_error(exc: Exception) -> bool:
    """Return True when the exception likely originates from a bad profile."""
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _PROFILE_RELATED_ERRORS)


def _is_browser_missing_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "browser" in msg and ("install" in msg or "executable" in msg or "not found" in msg)


class BrowserSession:
    """Thread-safe Playwright persistent-context session shared by all browser tooling.

    Lifecycle guarantees
    --------------------
    * ``ensure_context()`` is safe to call from any thread; concurrent callers
      block on a lock but never race.
    * If the primary persistent profile fails (stale / locked / corrupted) the
      session transparently retries with an ephemeral fallback profile.
    * ``ensure_page()`` detects a dead context and transparently re-initialises
      before retrying, so callers rarely need to handle browser-level errors.
    * ``close()`` is idempotent and always runs cleanup even after exceptions.
    * ``atexit`` ensures browser processes are reaped on interpreter shutdown.
    """

    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._playwright: Any = None
        self._context: Any = None
        self._settings = settings
        self._timeout_ms = safe_int(getattr(settings, "browser_automation_timeout_ms", 15000), 15000)
        raw_profile = str(getattr(settings, "browser_profile_dir", "data/browser_profile")).strip() or "data/browser_profile"
        self._profile_dir = Path(raw_profile).expanduser().resolve()
        self._fallback_dir: Path | None = None
        self._using_fallback = False
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        """Best-effort teardown.  Safe to call multiple times."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._playwright = None

    def _reset(self) -> None:
        """Tear down current state so the next ``ensure_context`` starts fresh."""
        self._close_locked()

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    # ------------------------------------------------------------------
    # context management
    # ------------------------------------------------------------------

    def ensure_context(self) -> Any:
        with self._lock:
            # Fast path: existing context is still usable.
            if self._context is not None:
                try:
                    # A lightweight probe — accessing .pages on a dead context
                    # raises immediately.
                    _ = self._context.pages
                    return self._context
                except Exception:
                    logger.warning("browser context died; re-initialising")
                    self._reset()

            return self._create_context_locked()

    def _create_context_locked(self) -> Any:
        """Launch a persistent context, falling back to a temp profile on failure.

        Must be called while ``self._lock`` is held.
        """
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(
                "Playwright is not available. Install dependency and run: playwright install chromium"
            ) from exc

        # Attempt 1: the user's persistent profile (preserves logins, cookies).
        ctx = self._try_launch(sync_playwright, self._profile_dir)
        if ctx is not None:
            self._using_fallback = False
            logger.info("browser context started (profile: %s)", self._profile_dir)
            return ctx

        # Attempt 2: ephemeral fallback profile (no login state, but functional).
        fallback = self._get_fallback_dir()
        logger.warning(
            "primary profile failed; retrying with fallback profile: %s", fallback,
        )
        ctx = self._try_launch(sync_playwright, fallback)
        if ctx is not None:
            self._using_fallback = True
            logger.info("browser context started (fallback profile)")
            return ctx

        # All attempts exhausted — surface a clear actionable error.
        raise RuntimeError(
            "Failed to start Playwright browser. "
            "The persistent profile may be locked or corrupted. "
            "Close all Chromium/Chrome windows and retry, or delete 'data/browser_profile' "
            "and run again. Also verify: playwright install chromium"
        )

    def _try_launch(self, sync_playwright_mod: Any, profile_dir: Path) -> Any:
        """Try to launch a persistent context with the given profile.

        Returns the context on success, ``None`` on profile-related or
        missing-browser errors (caller should try the next strategy).
        """
        profile_dir.mkdir(parents=True, exist_ok=True)
        pw = None
        try:
            pw = sync_playwright_mod.start()
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                no_viewport=True,
                args=["--disable-notifications", "--start-maximized"],
            )
            self._playwright = pw
            self._context = ctx
            return ctx
        except Exception as exc:
            if _is_browser_missing_error(exc):
                # No point retrying — the binary is gone.
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:
                        pass
                raise RuntimeError(
                    "Playwright browser executable is missing. Run: playwright install chromium"
                ) from exc
            if _is_profile_error(exc):
                logger.warning("profile launch failed (%s); will try fallback", exc)
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:
                        pass
                return None
            # Unknown error — still try fallback.
            logger.warning("unexpected browser launch error (%s); will try fallback", exc)
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            return None

    def _get_fallback_dir(self) -> Path:
        """Return (and lazily create) a temp directory for the fallback profile."""
        if self._fallback_dir is not None and self._fallback_dir.exists():
            return self._fallback_dir
        base = Path(tempfile.gettempdir()) / "jarvis_browser_fallback"
        base.mkdir(parents=True, exist_ok=True)
        # Use a stable directory name so concurrent sessions don't collide,
        # but add a PID suffix so separate JARVIS processes don't fight.
        import os

        self._fallback_dir = base / f"profile_{os.getpid()}"
        self._fallback_dir.mkdir(parents=True, exist_ok=True)
        return self._fallback_dir

    # ------------------------------------------------------------------
    # page management
    # ------------------------------------------------------------------

    def find_page(self, host_hints: list[str]) -> Any | None:
        context = self.ensure_context()
        lowered_hints = [item.lower() for item in host_hints]
        for page in context.pages:
            url = (page.url or "").lower()
            if any(hint in url for hint in lowered_hints):
                return page
        return None

    def ensure_page(self, url: str, host_hints: list[str]) -> Any:
        """Navigate to *url* and return the page, recovering from dead contexts.

        If the context has died between ``ensure_context`` returning and the
        actual navigation, we re-initialise transparently.
        """
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                context = self.ensure_context()
                page = self.find_page(host_hints)
                if page is None:
                    page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                page.bring_to_front()
                return page
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning(
                        "ensure_page failed on first attempt (%s); resetting context", exc,
                    )
                    with self._lock:
                        self._reset()
                    continue
        raise RuntimeError(
            f"Failed to open page after recovery: {last_error}"
        ) from last_error

    # ------------------------------------------------------------------
    # page helpers (thin wrappers that log and propagate)
    # ------------------------------------------------------------------

    def page_snapshot(self, page: Any) -> dict[str, Any]:
        try:
            return {"url": page.url, "title": page.title}
        except Exception:
            return {"url": "", "title": ""}

    def wait_for(self, page: Any, selector: str, timeout_ms: int | None = None) -> bool:
        page.wait_for_selector(selector, timeout=timeout_ms or self._timeout_ms)
        return True

    def fill(self, page: Any, selector: str, value: str) -> None:
        page.locator(selector).first.fill(value, timeout=self._timeout_ms)

    def click(self, page: Any, selector: str) -> None:
        page.locator(selector).first.click(timeout=self._timeout_ms)

    def press_key(self, page: Any, key: str) -> None:
        page.keyboard.press(key)

    def select_option(self, page: Any, selector: str, value: str) -> None:
        page.locator(selector).first.select_option(value, timeout=self._timeout_ms)

    def upload(self, page: Any, selector: str, file_path: str) -> None:
        page.locator(selector).first.set_input_files(file_path, timeout=self._timeout_ms)

    def extract_text(self, page: Any, selector: str, attribute: str = "text", multiple: bool = False) -> list[str]:
        locator = page.locator(selector)
        if not multiple:
            locator = locator.first
        count = min(50, locator.count())
        values: list[str] = []
        for index in range(count):
            element = locator.nth(index)
            try:
                if attribute == "href":
                    raw = element.get_attribute("href") or ""
                elif attribute == "text":
                    raw = element.inner_text() or ""
                else:
                    raw = element.get_attribute(attribute) or ""
                value = raw.strip()
                if value:
                    values.append(value)
            except Exception:
                continue
        return values

    def evaluate(self, page: Any, expression: str, arg: Any = None) -> Any:
        return page.evaluate(expression, arg)

    def screenshot(self, page: Any, output_path: str) -> dict[str, Any]:
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path))
        return {"path": str(path)}


_session_singleton: BrowserSession | None = None
_session_lock = threading.Lock()


def get_browser_session(settings: Settings) -> BrowserSession:
    global _session_singleton
    if _session_singleton is None:
        with _session_lock:
            if _session_singleton is None:
                _session_singleton = BrowserSession(settings)
    return _session_singleton
