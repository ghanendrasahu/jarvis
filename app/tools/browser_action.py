from __future__ import annotations

from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.utils import safe_int
from app.tools.browser_session import BrowserSession, get_browser_session
from app.tools.registry import ToolRegistry

logger = get_logger("browser_action")

_ALLOWED_ACTIONS = {
    "goto",
    "wait_for",
    "fill",
    "click",
    "select",
    "upload",
    "press",
    "extract",
    "evaluate",
    "screenshot",
    "open_new",
}


class BrowserActionError(RuntimeError):
    """Raised when a browser_action run fails mid-way.

    Carries a structured ``payload`` (steps_completed, page snapshot, screenshot)
    so callers can surface diagnostics instead of a bare message.
    """

    def __init__(self, message: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.payload = payload


def _normalize_steps(raw_steps: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("browser_action requires a non-empty 'steps' list.")
    steps: list[dict[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            raise ValueError("Each browser step must be an object.")
        action = str(item.get("action", "")).strip().lower()
        if not action:
            raise ValueError("Each browser step requires an 'action'.")
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported browser action: {action}")
        steps.append(item)
    return steps


class BrowserActionTool:
    def __init__(self, settings: Settings, session: BrowserSession | None = None) -> None:
        self.settings = settings
        self.session = session or get_browser_session(settings)

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        steps = _normalize_steps(args.get("steps"))
        host_hints = [str(h).strip().lower() for h in (args.get("host_hints") or []) if str(h).strip()]
        page = None
        results: list[dict[str, Any]] = []
        screenshot_path = str(args.get("screenshot_on_failure", "data/browser_action_failure.png"))

        try:
            for index, step in enumerate(steps):
                action = str(step.get("action", "")).lower()
                step_result: dict[str, Any] = {"step": index + 1, "action": action}

                if action == "goto":
                    url = str(step.get("url", "")).strip()
                    if not url:
                        raise ValueError("goto requires a 'url'.")
                    page = self.session.ensure_page(url, host_hints or ["http"])
                    step_result["page"] = self.session.page_snapshot(page)

                elif action == "open_new":
                    url = str(step.get("url", "")).strip()
                    context = self.session.ensure_context()
                    page = context.new_page()
                    if url:
                        page.goto(url, wait_until="domcontentloaded", timeout=self.session.timeout_ms)
                        page.bring_to_front()
                    step_result["page"] = self.session.page_snapshot(page)

                elif action == "wait_for":
                    self._require_page(page)
                    selector = str(step.get("selector", "")).strip()
                    if not selector:
                        raise ValueError("wait_for requires a 'selector'.")
                    timeout_ms = safe_int(step.get("timeout_ms", self.session.timeout_ms), self.session.timeout_ms)
                    self.session.wait_for(page, selector, timeout_ms=max(500, timeout_ms))
                    step_result["waited_for"] = selector

                elif action == "fill":
                    self._require_page(page)
                    selector = str(step.get("selector", "")).strip()
                    value = str(step.get("value", ""))
                    if not selector:
                        raise ValueError("fill requires a 'selector'.")
                    self.session.fill(page, selector, value)
                    step_result["filled"] = selector

                elif action == "click":
                    self._require_page(page)
                    selector = str(step.get("selector", "")).strip()
                    if not selector:
                        raise ValueError("click requires a 'selector'.")
                    self.session.click(page, selector)
                    step_result["clicked"] = selector

                elif action == "select":
                    self._require_page(page)
                    selector = str(step.get("selector", "")).strip()
                    value = str(step.get("value", "")).strip()
                    if not selector or not value:
                        raise ValueError("select requires 'selector' and 'value'.")
                    self.session.select_option(page, selector, value)
                    step_result["selected"] = selector

                elif action == "upload":
                    self._require_page(page)
                    selector = str(step.get("selector", "")).strip()
                    path = str(step.get("path", "")).strip()
                    if not selector or not path:
                        raise ValueError("upload requires 'selector' and 'path'.")
                    from app.core.safe_paths import resolve_safe_path

                    resolved_path = resolve_safe_path(path, self.settings, must_exist=True)
                    self.session.upload(page, selector, str(resolved_path))
                    step_result["uploaded"] = str(resolved_path)

                elif action == "press":
                    self._require_page(page)
                    key = str(step.get("key", "")).strip()
                    if not key:
                        raise ValueError("press requires a 'key'.")
                    self.session.press_key(page, key)
                    step_result["pressed"] = key

                elif action == "extract":
                    self._require_page(page)
                    selector = str(step.get("selector", "")).strip()
                    attribute = str(step.get("attribute", "text")).strip()
                    multiple = bool(step.get("multiple", False))
                    if not selector:
                        raise ValueError("extract requires a 'selector'.")
                    values = self.session.extract_text(page, selector, attribute=attribute, multiple=multiple)
                    step_result["count"] = len(values)
                    step_result["values"] = values[:50]

                elif action == "evaluate":
                    self._require_page(page)
                    expression = str(step.get("expression", "")).strip()
                    if not expression:
                        raise ValueError("evaluate requires an 'expression'.")
                    value = self.session.evaluate(page, expression, step.get("arg"))
                    step_result["value"] = value

                elif action == "screenshot":
                    self._require_page(page)
                    path = str(step.get("path", screenshot_path)).strip() or screenshot_path
                    saved = self.session.screenshot(page, path)
                    step_result["screenshot"] = saved["path"]

                results.append(step_result)

            snapshot = self.session.page_snapshot(page) if page is not None else {}
            return {
                "status": "completed",
                "steps": results,
                "page": snapshot,
                "engine": "playwright",
            }
        except Exception as exc:
            logger.warning("browser_action failed: %s", exc)
            error_payload: dict[str, Any] = {
                "status": "failed",
                "error": str(exc),
                "steps_completed": results,
            }
            if page is not None:
                error_payload["page"] = self.session.page_snapshot(page)
                try:
                    self.session.screenshot(page, screenshot_path)
                    error_payload["screenshot"] = screenshot_path
                except Exception:
                    pass
            raise BrowserActionError(str(exc), payload=error_payload) from exc

    @staticmethod
    def _require_page(page: Any | None) -> None:
        if page is None:
            raise ValueError("No browser page yet. Add a 'goto' or 'open_new' step first.")


def register_browser_action(registry: ToolRegistry, settings: Settings) -> None:
    tool = BrowserActionTool(settings)

    def browser_action_handler(args: dict[str, Any]) -> dict[str, Any]:
        return tool.run(args)

    registry.register(
        name="browser_action",
        description=(
            "Drive a real browser step-by-step. 'steps' is a list of {action, selector/value/...}. "
            "Actions: goto(url), open_new(url), wait_for(selector), fill(selector, value), click(selector), "
            "select(selector, value), upload(selector, path), press(key), extract(selector, attribute, multiple), "
            "evaluate(expression), screenshot(path). Returns extracted data and page URL."
        ),
        risk="medium",
        parameters={
            "steps": "list",
            "url": "string",
            "host_hints": "list[string]",
            "screenshot_on_failure": "string",
        },
        handler=browser_action_handler,
    )
