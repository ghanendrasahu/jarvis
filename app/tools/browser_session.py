from __future__ import annotations

import atexit
import threading
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.utils import safe_int

logger = get_logger("browser_session")


class BrowserSession:
    """Thread-safe Playwright persistent-context session shared by all browser tooling."""

    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._playwright = None
        self._context = None
        self._settings = settings
        self._timeout_ms = safe_int(getattr(settings, "browser_automation_timeout_ms", 15000), 15000)
        raw_profile = str(getattr(settings, "browser_profile_dir", "data/browser_profile")).strip() or "data/browser_profile"
        self._profile_dir = Path(raw_profile).expanduser().resolve()
        atexit.register(self.close)

    def close(self) -> None:
        with self._lock:
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

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def ensure_context(self):
        with self._lock:
            if self._context is not None:
                return self._context
            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                raise RuntimeError(
                    "Playwright is not available. Install dependency and run: playwright install chromium"
                ) from exc

            self._profile_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = sync_playwright().start()
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    headless=False,
                    no_viewport=True,
                    args=["--disable-notifications", "--start-maximized"],
                )
            except Exception as exc:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._context = None
                raise RuntimeError(f"Failed to start Playwright browser context: {exc}") from exc
            logger.info("playwright browser context started")
            return self._context

    def find_page(self, host_hints: list[str]) -> Any | None:
        context = self.ensure_context()
        lowered_hints = [item.lower() for item in host_hints]
        for page in context.pages:
            url = (page.url or "").lower()
            if any(hint in url for hint in lowered_hints):
                return page
        return None

    def ensure_page(self, url: str, host_hints: list[str]) -> Any:
        context = self.ensure_context()
        page = self.find_page(host_hints)
        if page is None:
            page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        page.bring_to_front()
        return page

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
