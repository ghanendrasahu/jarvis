from __future__ import annotations

import atexit
import re
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import requests

from app.config import Settings
from app.tools.registry import ToolRegistry


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_duration_to_seconds(raw_value: str) -> int:
    text = raw_value.strip().lower()
    if not text:
        raise ValueError("Missing timestamp.")

    match = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", text)
    if match:
        first = int(match.group(1))
        second = int(match.group(2))
        third = match.group(3)
        if third is None:
            return (first * 60) + second
        return (first * 3600) + (second * 60) + int(third)

    unit_pattern = re.compile(
        r"(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*"
        r"(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
        r"(?:(?P<seconds>\d+)\s*s(?:ec(?:ond)?s?)?)?$"
    )
    unit_match = unit_pattern.fullmatch(text)
    if unit_match and any(unit_match.groupdict().values()):
        hours = int(unit_match.group("hours") or 0)
        minutes = int(unit_match.group("minutes") or 0)
        seconds = int(unit_match.group("seconds") or 0)
        return (hours * 3600) + (minutes * 60) + seconds

    if text.isdigit():
        return int(text)

    raise ValueError(f"Unsupported timestamp format: '{raw_value}'")


def _append_start_time(url: str, start_seconds: int) -> str:
    if start_seconds <= 0:
        return url
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["t"] = [f"{start_seconds}s"]
    rebuilt = parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
    return urllib.parse.urlunparse(rebuilt)


def _first_youtube_video_url(query: str) -> tuple[str | None, str]:
    encoded = urllib.parse.quote_plus(query)
    search_url = f"https://www.youtube.com/results?search_query={encoded}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception:
        return None, search_url

    matches = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', response.text)
    seen: set[str] = set()
    for candidate in matches:
        if candidate in seen:
            continue
        seen.add(candidate)
        return candidate, f"https://www.youtube.com/watch?v={candidate}&autoplay=1"
    return None, search_url


def _focus_window(title_hints: list[str]) -> str | None:
    import pygetwindow as gw

    lowered_hints = [hint.strip().lower() for hint in title_hints if hint.strip()]
    if not lowered_hints:
        return None

    for window in gw.getAllWindows():
        title = (window.title or "").strip()
        lowered = title.lower()
        if any(hint in lowered for hint in lowered_hints):
            window.activate()
            return title
    return None


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True
    return pyautogui


def _read_clipboard_text() -> str:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        value = root.clipboard_get()
        root.destroy()
        return str(value).strip()
    except Exception:
        return ""


class _PlaywrightAutomation:
    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._playwright = None
        self._context = None
        self._settings = settings
        self._timeout_ms = _safe_int(getattr(settings, "browser_automation_timeout_ms", 15000), 15000)
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

    def _ensure_context(self):
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
                    args=[
                        "--disable-notifications",
                        "--start-maximized",
                    ],
                )
            except Exception as exc:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._context = None
                raise RuntimeError(f"Failed to start Playwright browser context: {exc}") from exc
            return self._context

    def _find_page(self, host_hints: list[str]) -> Any | None:
        context = self._ensure_context()
        lowered_hints = [item.lower() for item in host_hints]
        for page in context.pages:
            url = (page.url or "").lower()
            if any(hint in url for hint in lowered_hints):
                return page
        return None

    def _ensure_page(self, url: str, host_hints: list[str]) -> Any:
        context = self._ensure_context()
        page = self._find_page(host_hints)
        if page is None:
            page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        page.bring_to_front()
        return page

    def open_youtube_video(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_url = str(args.get("url", "")).strip()
        query = str(args.get("query", "")).strip()
        start_seconds = max(0, _safe_int(args.get("start_seconds", 0), 0))
        prefer_first = bool(args.get("prefer_first_result", True))

        page = self._ensure_page("https://www.youtube.com", ["youtube.com", "youtu.be"])
        resolved_video_id: str | None = None

        if raw_url:
            url = raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}"
            page.goto(_append_start_time(url, start_seconds), wait_until="domcontentloaded", timeout=self._timeout_ms)
        elif query:
            encoded = urllib.parse.quote_plus(query)
            if prefer_first:
                search_url = f"https://www.youtube.com/results?search_query={encoded}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                page.wait_for_selector("ytd-video-renderer a#video-title", timeout=self._timeout_ms)
                first = page.locator("ytd-video-renderer a#video-title").first
                href = first.get_attribute("href") or ""
                if href:
                    parsed = urllib.parse.urlparse(href)
                    query_values = urllib.parse.parse_qs(parsed.query)
                    video_id_values = query_values.get("v") or []
                    if video_id_values:
                        resolved_video_id = video_id_values[0]
                    target = urllib.parse.urljoin("https://www.youtube.com", href)
                    page.goto(_append_start_time(target, start_seconds), wait_until="domcontentloaded", timeout=self._timeout_ms)
                else:
                    first.click(timeout=self._timeout_ms)
            else:
                page.goto(
                    f"https://www.youtube.com/results?search_query={encoded}",
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )
        else:
            page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=self._timeout_ms)

        return {
            "opened": page.url,
            "query": query or None,
            "resolved_video_id": resolved_video_id,
            "start_seconds": start_seconds,
            "engine": "playwright",
        }

    def control_youtube(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", "")).strip().lower()
        repeat = max(1, _safe_int(args.get("repeat", 1), 1))
        jump_percent = args.get("jump_percent")

        if not action:
            raise ValueError("Missing action.")

        page = self._find_page(["youtube.com/watch", "youtu.be/"])
        if page is None:
            raise RuntimeError("No active YouTube watch page found in Playwright browser.")
        page.bring_to_front()
        page.wait_for_selector("video", timeout=self._timeout_ms)

        if action in {"next", "next_video"}:
            page.keyboard.press("Shift+N")
        elif action in {"previous", "prev", "previous_video"}:
            page.keyboard.press("Shift+P")
        elif action in {"fullscreen", "full_screen"}:
            page.keyboard.press("f")
        elif action in {"captions", "subtitle", "cc"}:
            page.keyboard.press("c")
        elif action in {"mini_player", "miniplayer"}:
            page.keyboard.press("i")
        elif action in {"search"}:
            page.keyboard.press("/")
        elif action in {"speed_up", "faster"}:
            page.evaluate(
                "(n) => { const v = document.querySelector('video'); if (!v) return false; "
                "v.playbackRate = Math.min(4, v.playbackRate + (0.25 * n)); return true; }",
                repeat,
            )
        elif action in {"speed_down", "slower"}:
            page.evaluate(
                "(n) => { const v = document.querySelector('video'); if (!v) return false; "
                "v.playbackRate = Math.max(0.25, v.playbackRate - (0.25 * n)); return true; }",
                repeat,
            )
        elif action in {"jump", "jump_percent"}:
            if jump_percent is None:
                raise ValueError("jump_percent is required for jump action.")
            percent = max(0, min(100, _safe_int(jump_percent, 0)))
            page.evaluate(
                "(p) => { const v = document.querySelector('video'); if (!v || !isFinite(v.duration) || v.duration <= 0) return false; "
                "v.currentTime = v.duration * (p / 100); return true; }",
                percent,
            )
        else:
            # Default controls use direct media manipulation for reliability.
            page.evaluate(
                """
                ({ action, repeat }) => {
                    const v = document.querySelector('video');
                    if (!v) return { ok: false, reason: 'no_video' };
                    switch (action) {
                        case 'play':
                            v.play();
                            return { ok: true };
                        case 'pause':
                            v.pause();
                            return { ok: true };
                        case 'play_pause':
                        case 'toggle':
                            if (v.paused) { v.play(); } else { v.pause(); }
                            return { ok: true };
                        case 'forward':
                        case 'skip':
                        case 'skip_forward':
                            v.currentTime = Math.min(v.duration || Number.MAX_SAFE_INTEGER, v.currentTime + (10 * repeat));
                            return { ok: true };
                        case 'back':
                        case 'backward':
                        case 'rewind':
                            v.currentTime = Math.max(0, v.currentTime - (10 * repeat));
                            return { ok: true };
                        case 'mute':
                        case 'unmute':
                        case 'mute_toggle':
                            v.muted = !v.muted;
                            return { ok: true };
                        case 'volume_up':
                        case 'louder':
                            v.volume = Math.min(1, v.volume + (0.05 * repeat));
                            return { ok: true };
                        case 'volume_down':
                        case 'quieter':
                            v.volume = Math.max(0, v.volume - (0.05 * repeat));
                            return { ok: true };
                        case 'start':
                        case 'beginning':
                            v.currentTime = 0;
                            return { ok: true };
                        case 'end':
                            if (isFinite(v.duration)) {
                                v.currentTime = Math.max(0, v.duration - 1);
                                return { ok: true };
                            }
                            return { ok: false, reason: 'duration_unknown' };
                        default:
                            return { ok: false, reason: 'unsupported_action' };
                    }
                }
                """,
                {"action": action, "repeat": repeat},
            )

        media_state = page.evaluate(
            "() => { const v = document.querySelector('video'); "
            "if (!v) return null; "
            "return { currentTime: v.currentTime, duration: v.duration, paused: v.paused, muted: v.muted, volume: v.volume, playbackRate: v.playbackRate }; }"
        )
        return {
            "action": action,
            "repeat": repeat,
            "url": page.url,
            "engine": "playwright",
            "media_state": media_state,
        }

    def seek_youtube(self, args: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(args.get("timestamp", "")).strip()
        if not timestamp:
            raise ValueError("Missing timestamp.")
        seconds = max(0, _parse_duration_to_seconds(timestamp))

        page = self._find_page(["youtube.com/watch", "youtu.be/"])
        if page is None:
            raise RuntimeError("No active YouTube watch page found in Playwright browser.")
        page.bring_to_front()
        page.wait_for_selector("video", timeout=self._timeout_ms)
        success = page.evaluate(
            "(s) => { const v = document.querySelector('video'); "
            "if (!v) return false; "
            "v.currentTime = s; return true; }",
            seconds,
        )
        if not success:
            raise RuntimeError("Could not seek current YouTube video.")
        return {
            "timestamp": timestamp,
            "seconds": seconds,
            "url": page.url,
            "engine": "playwright",
        }

    def open_whatsapp_chat(self, args: dict[str, Any]) -> dict[str, Any]:
        phone = str(args.get("phone", "")).strip()
        message = str(args.get("message", "")).strip()
        send_now = bool(args.get("send_now", False))

        if not phone:
            raise ValueError("Missing phone number.")
        normalized_phone = re.sub(r"[^\d]", "", phone)
        if not normalized_phone:
            raise ValueError("Phone number must include digits.")

        page = self._ensure_page("https://web.whatsapp.com", ["web.whatsapp.com"])
        query: dict[str, str] = {"phone": normalized_phone}
        if message:
            query["text"] = message
        target_url = "https://web.whatsapp.com/send?" + urllib.parse.urlencode(query)
        page.goto(target_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        page.bring_to_front()

        # Logged-out session shows QR code. In that case we open but do not attempt send.
        qr_locator = page.locator("[data-testid='qrcode'], canvas[aria-label*='Scan me']")
        if qr_locator.count() > 0:
            return {
                "opened": page.url,
                "phone": normalized_phone,
                "message_length": len(message),
                "send_now": False,
                "logged_in": False,
                "engine": "playwright",
            }

        if send_now:
            try:
                page.wait_for_selector("footer", timeout=self._timeout_ms)
                page.keyboard.press("Enter")
            except Exception as exc:
                raise RuntimeError(f"Failed to send WhatsApp message automatically: {exc}") from exc

        return {
            "opened": page.url,
            "phone": normalized_phone,
            "message_length": len(message),
            "send_now": send_now,
            "logged_in": True,
            "engine": "playwright",
        }


def register_browser_tools(registry: ToolRegistry, settings: Settings) -> None:
    automation = _PlaywrightAutomation(settings)

    def open_url(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", "")).strip()
        if not url:
            raise ValueError("Missing URL.")
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url
        webbrowser.open(url, new=2)
        return {"opened": url}

    def search_web(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("Missing search query.")
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded}"
        webbrowser.open(url, new=2)
        return {"query": query, "opened": url}

    def open_youtube_video(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return automation.open_youtube_video(args)
        except Exception:
            raw_url = str(args.get("url", "")).strip()
            query = str(args.get("query", "")).strip()
            start_seconds = max(0, _safe_int(args.get("start_seconds", 0), 0))
            prefer_first = bool(args.get("prefer_first_result", True))

            opened_url = ""
            resolved_video_id: str | None = None
            if raw_url:
                url = raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}"
                opened_url = _append_start_time(url, start_seconds)
            elif query:
                if prefer_first:
                    resolved_video_id, opened_url = _first_youtube_video_url(query)
                    opened_url = _append_start_time(opened_url, start_seconds)
                else:
                    encoded = urllib.parse.quote_plus(query)
                    opened_url = f"https://www.youtube.com/results?search_query={encoded}"
            else:
                opened_url = "https://www.youtube.com"
            webbrowser.open(opened_url, new=2)
            return {
                "opened": opened_url,
                "query": query or None,
                "resolved_video_id": resolved_video_id,
                "start_seconds": start_seconds,
                "engine": "webbrowser_fallback",
            }

    def control_youtube(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return automation.control_youtube(args)
        except Exception:
            action = str(args.get("action", "")).strip().lower()
            repeat = max(1, _safe_int(args.get("repeat", 1), 1))
            jump_percent = args.get("jump_percent")
            pre_delay = float(args.get("pre_delay_seconds", 0.2))

            if not action:
                raise ValueError("Missing action.")

            key_sequences: list[list[str]]
            if action in {"play", "pause", "play_pause", "toggle"}:
                key_sequences = [["k"]]
            elif action in {"next", "next_video"}:
                key_sequences = [["shift", "n"]]
            elif action in {"previous", "prev", "previous_video"}:
                key_sequences = [["shift", "p"]]
            elif action in {"forward", "skip", "skip_forward"}:
                key_sequences = [["l"]] * repeat
            elif action in {"back", "backward", "rewind"}:
                key_sequences = [["j"]] * repeat
            elif action in {"mute", "unmute", "mute_toggle"}:
                key_sequences = [["m"]]
            elif action in {"fullscreen", "full_screen"}:
                key_sequences = [["f"]]
            elif action in {"captions", "subtitle", "cc"}:
                key_sequences = [["c"]]
            elif action in {"speed_up", "faster"}:
                key_sequences = [["shift", "."]] * repeat
            elif action in {"speed_down", "slower"}:
                key_sequences = [["shift", ","]] * repeat
            elif action in {"volume_up", "louder"}:
                key_sequences = [["up"]] * repeat
            elif action in {"volume_down", "quieter"}:
                key_sequences = [["down"]] * repeat
            elif action in {"start", "beginning"}:
                key_sequences = [["0"]]
            elif action in {"end"}:
                key_sequences = [["end"]]
            elif action in {"mini_player", "miniplayer"}:
                key_sequences = [["i"]]
            elif action in {"search"}:
                key_sequences = [["/"]]
            elif action in {"jump", "jump_percent"}:
                if jump_percent is None:
                    raise ValueError("jump_percent is required for jump action.")
                percent = _safe_int(jump_percent, 0)
                digit = "0" if percent <= 0 else str(max(1, min(9, round(percent / 10))))
                key_sequences = [[digit]]
            else:
                raise ValueError(f"Unsupported YouTube action: {action}")

            focused = _focus_window(["youtube", " - youtube"])
            if focused is None:
                raise RuntimeError("No YouTube window found to control.")
            time.sleep(max(0, pre_delay))
            pyautogui = _pyautogui()
            for sequence in key_sequences:
                if len(sequence) == 1:
                    pyautogui.press(sequence[0])
                else:
                    pyautogui.hotkey(*sequence)
            return {
                "action": action,
                "repeat": repeat,
                "focused_window": focused,
                "key_sequences": key_sequences,
                "engine": "keyboard_fallback",
            }

    def seek_youtube(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return automation.seek_youtube(args)
        except Exception:
            timestamp = str(args.get("timestamp", "")).strip()
            if not timestamp:
                raise ValueError("Missing timestamp.")
            seconds = max(0, _parse_duration_to_seconds(timestamp))
            focused = _focus_window(["youtube", " - youtube"])
            if focused is None:
                raise RuntimeError("No YouTube window found to seek.")
            time.sleep(float(args.get("pre_delay_seconds", 0.2)))

            pyautogui = _pyautogui()
            pyautogui.hotkey("ctrl", "l")
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.1)
            current_url = _read_clipboard_text()
            if not current_url.startswith(("http://", "https://")):
                raise RuntimeError("Unable to read current browser URL for YouTube seeking.")
            parsed_current = urllib.parse.urlparse(current_url)
            host = (parsed_current.netloc or "").lower()
            if "youtube.com" not in host and "youtu.be" not in host:
                raise RuntimeError("Active browser tab is not a YouTube URL.")
            if "youtube.com" in host and "/watch" not in parsed_current.path:
                raise RuntimeError("Active YouTube tab is not a watch page.")
            url = _append_start_time(current_url, seconds)
            pyautogui.write(url, interval=0.01)
            pyautogui.press("enter")
            return {
                "timestamp": timestamp,
                "seconds": seconds,
                "focused_window": focused,
                "navigated_to": url,
                "engine": "keyboard_fallback",
            }

    def open_whatsapp_chat(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return automation.open_whatsapp_chat(args)
        except Exception:
            phone = str(args.get("phone", "")).strip()
            message = str(args.get("message", "")).strip()
            send_now = bool(args.get("send_now", False))
            pre_delay = float(args.get("pre_delay_seconds", 4.0))
            if not phone:
                raise ValueError("Missing phone number.")
            normalized_phone = re.sub(r"[^\d]", "", phone)
            if not normalized_phone:
                raise ValueError("Phone number must include digits.")
            query: dict[str, str] = {"phone": normalized_phone}
            if message:
                query["text"] = message
            url = "https://web.whatsapp.com/send?" + urllib.parse.urlencode(query)
            webbrowser.open(url, new=2)
            focused = None
            sent = False
            if send_now:
                time.sleep(max(0, pre_delay))
                focused = _focus_window(["whatsapp"])
                if focused is None:
                    raise RuntimeError("Could not focus WhatsApp window to send message.")
                pyautogui = _pyautogui()
                pyautogui.press("enter")
                sent = True
            return {
                "opened": url,
                "phone": normalized_phone,
                "message_length": len(message),
                "send_now": sent,
                "focused_window": focused,
                "engine": "keyboard_fallback",
            }

    registry.register(
        name="open_url",
        description="Open a URL in the default browser.",
        risk="low",
        parameters={"url": "string"},
        handler=open_url,
    )
    registry.register(
        name="search_web",
        description="Search the web in browser.",
        risk="low",
        parameters={"query": "string"},
        handler=search_web,
    )
    registry.register(
        name="open_youtube_video",
        description="Open YouTube and play a specific video URL or top search result for a query.",
        risk="low",
        parameters={
            "query": "string",
            "url": "string",
            "start_seconds": "integer",
            "prefer_first_result": "boolean",
        },
        handler=open_youtube_video,
    )
    registry.register(
        name="control_youtube",
        description="Control YouTube playback (Playwright first, keyboard fallback).",
        risk="low",
        parameters={
            "action": "string",
            "repeat": "integer",
            "jump_percent": "integer",
            "pre_delay_seconds": "number",
        },
        handler=control_youtube,
    )
    registry.register(
        name="seek_youtube",
        description="Seek YouTube playback to a timestamp.",
        risk="low",
        parameters={"timestamp": "string", "pre_delay_seconds": "number"},
        handler=seek_youtube,
    )
    registry.register(
        name="open_whatsapp_chat",
        description="Open a WhatsApp Web chat with optional prefilled message and send.",
        risk="low",
        parameters={
            "phone": "string",
            "message": "string",
            "send_now": "boolean",
            "pre_delay_seconds": "number",
        },
        handler=open_whatsapp_chat,
    )
