from __future__ import annotations

import re
from typing import Any

from app.core.utils import parse_duration_to_seconds
from app.models.schemas import Action
from app.tools.system_tools import resolve_open_target

_WORD_ALIASES = {
    "youutube": "youtube",
    "youtub": "youtube",
    "you tube": "youtube",
    "utube": "youtube",
    "whats app": "whatsapp",
    "whatsap": "whatsapp",
    "watsapp": "whatsapp",
    "olay": "play",
    "paly": "play",
    "previos": "previous",
    "previus": "previous",
    "nxt": "next",
    "skp": "skip",
    "rewimd": "rewind",
    "seak": "seek",
    "mesage": "message",
}


def _normalize_command(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    normalized = lowered
    for source, target in _WORD_ALIASES.items():
        pattern = r"\b" + re.escape(source) + r"\b"
        normalized = re.sub(pattern, target, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _is_youtube_followup(text: str) -> bool:
    return bool(
        re.fullmatch(r"(play|pause|resume|next|previous|prev|mute|unmute|fullscreen|captions|subtitle|faster|slower)", text)
        or re.fullmatch(r"(skip|forward|rewind|back)\s+\d+(?::\d{1,2}(?::\d{1,2})?)?", text)
        or re.fullmatch(r"(seek|go to)\s+.+", text)
    )


class DirectCommandRouter:
    """Fast-path parser for deterministic commands before LLM planning."""

    def route(self, command: str, context: dict[str, Any] | None = None) -> list[Action]:
        text = command.strip()
        if not text:
            return []

        normalized = _normalize_command(text)
        active_surface = str((context or {}).get("active_surface") or "").strip().lower()

        # Context-aware follow-ups for YouTube.
        if active_surface == "youtube" and _is_youtube_followup(normalized):
            normalized = f"youtube {normalized}"

        if normalized in {"list shortcuts", "show shortcuts", "list desktop shortcuts", "shortcut list"}:
            return [Action(tool="list_app_shortcuts", args={"limit": 200}, risk="low", reason="List available shortcuts")]

        if normalized in {"list apps", "show apps", "list known apps", "app list"}:
            return [Action(tool="list_known_apps", args={}, risk="low", reason="List known app aliases")]

        if normalized in {"metrics", "show metrics", "dashboard", "show dashboard"}:
            return [Action(tool="get_runtime_metrics", args={"limit": 200}, risk="low", reason="View runtime metrics")]

        match = re.match(r"^(?:play\s+)?youtube\s+(https?://\S+)$", normalized, flags=re.IGNORECASE)
        if match:
            return [
                Action(
                    tool="open_youtube_video",
                    args={"url": match.group(1).strip()},
                    risk="low",
                    reason="Open YouTube video URL",
                )
            ]

        match = re.match(r"^play\s+(.+?)\s+on\s+youtube$", normalized, flags=re.IGNORECASE)
        if match:
            query = match.group(1).strip()
            if query:
                return [
                    Action(
                        tool="open_youtube_video",
                        args={"query": query, "prefer_first_result": True},
                        risk="low",
                        reason="Play requested YouTube video by query",
                    )
                ]

        match = re.match(r"^youtube\s+play\s+(.+)$", normalized, flags=re.IGNORECASE)
        if match:
            query = match.group(1).strip()
            if query:
                return [
                    Action(
                        tool="open_youtube_video",
                        args={"query": query, "prefer_first_result": True},
                        risk="low",
                    reason="Play requested YouTube video by query",
                )
            ]

        match = re.match(r"^(?:open\s+)?youtube(?:\s+and)?\s+play\s+(.+)$", normalized, flags=re.IGNORECASE)
        if match:
            query = match.group(1).strip()
            if query:
                return [
                    Action(
                        tool="open_youtube_video",
                        args={"query": query, "prefer_first_result": True},
                        risk="low",
                        reason="Open YouTube and play requested video by query",
                    )
                ]

        match = re.match(
            r"^(?:youtube\s+)?(?:seek|go to)\s+(.+?)(?:\s+in\s+youtube)?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            timestamp = match.group(1).strip()
            if timestamp:
                return [
                    Action(
                        tool="seek_youtube",
                        args={"timestamp": timestamp},
                        risk="low",
                        reason="Seek YouTube video to timestamp",
                    )
                ]

        match = re.match(
            r"^(?:youtube\s+)?(?:skip|forward)\s+(\d+(?::\d{1,2}(?::\d{1,2})?)?)\s*(?:sec|secs|seconds|s)?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            try:
                seconds = parse_duration_to_seconds(match.group(1))
            except ValueError:
                seconds = 0
            if seconds and seconds > 0:
                return [
                    Action(
                        tool="control_youtube",
                        args={"action": "skip_forward", "repeat": max(1, round(seconds / 10))},
                        risk="low",
                        reason="Skip forward on YouTube",
                    )
                ]

        match = re.match(
            r"^(?:youtube\s+)?(?:back|rewind)\s+(\d+(?::\d{1,2}(?::\d{1,2})?)?)\s*(?:sec|secs|seconds|s)?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            try:
                seconds = parse_duration_to_seconds(match.group(1))
            except ValueError:
                seconds = 0
            if seconds and seconds > 0:
                return [
                    Action(
                        tool="control_youtube",
                        args={"action": "backward", "repeat": max(1, round(seconds / 10))},
                        risk="low",
                        reason="Rewind on YouTube",
                    )
                ]

        match = re.match(r"^(?:youtube\s+)?jump\s+to\s+(\d{1,3})\s*%$", normalized, flags=re.IGNORECASE)
        if match:
            percent = int(match.group(1))
            percent = max(0, min(100, percent))
            return [
                Action(
                    tool="control_youtube",
                    args={"action": "jump", "jump_percent": percent},
                    risk="low",
                    reason="Jump to approximate section in YouTube video",
                )
            ]

        youtube_action_map = {
            "youtube pause": "pause",
            "youtube play": "play",
            "youtube resume": "play",
            "youtube next": "next_video",
            "youtube next video": "next_video",
            "youtube previous": "previous_video",
            "youtube prev": "previous_video",
            "youtube previous video": "previous_video",
            "youtube mute": "mute",
            "youtube unmute": "mute",
            "youtube fullscreen": "fullscreen",
            "youtube captions": "captions",
            "youtube subtitle": "captions",
            "youtube speed up": "speed_up",
            "youtube faster": "speed_up",
            "youtube slow down": "speed_down",
            "youtube slower": "speed_down",
            "youtube volume up": "volume_up",
            "youtube volume down": "volume_down",
            "youtube start": "start",
            "youtube beginning": "start",
            "youtube end": "end",
        }
        mapped_action = youtube_action_map.get(normalized)
        if mapped_action:
            return [
                Action(
                    tool="control_youtube",
                    args={"action": mapped_action},
                    risk="low",
                    reason="Control YouTube playback",
                )
            ]

        match = re.match(
            r"^(?:send\s+)?whatsapp(?:\s+message)?\s+to\s+(.+?)\s*[:\-]\s*(.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            recipient = match.group(1).strip()
            message = match.group(2).strip()
            if recipient and message:
                return [
                    Action(
                        tool="open_whatsapp_chat",
                        args={"phone": recipient, "message": message, "send_now": True},
                        risk="medium",
                        reason="Open WhatsApp chat and send message",
                    )
                ]

        match = re.match(
            r"^open\s+whatsapp\s+chat\s+(.+?)\s*[:\-]\s*(.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            recipient = match.group(1).strip()
            message = match.group(2).strip()
            if recipient:
                return [
                    Action(
                        tool="open_whatsapp_chat",
                        args={"phone": recipient, "message": message, "send_now": False},
                        risk="low",
                        reason="Open WhatsApp chat",
                    )
                ]

        match = re.match(r"^open shortcut (.+)$", normalized, flags=re.IGNORECASE)
        if match:
            shortcut_name = match.group(1).strip()
            if shortcut_name:
                return [
                    Action(
                        tool="open_application",
                        args={"name": shortcut_name},
                        risk="low",
                        reason="Open desktop shortcut by name",
                    )
                ]

        if normalized.startswith("open "):
            target_name = normalized[5:].strip()
            if target_name:
                resolved_target = resolve_open_target(target_name)
                if resolved_target and resolved_target["kind"] == "url":
                    return [
                        Action(
                            tool="open_url",
                            args={"url": resolved_target["target"]},
                            risk="low",
                            reason="Open web app",
                        )
                    ]

        match = re.match(r"^(open|launch|start)\s+(.+)$", normalized)
        if match:
            app_name = normalized.split(maxsplit=1)[1]
            # Safety: only route targets the known-resolution system recognizes
            # (app aliases / web apps / URLs). Unknown targets fall through to
            # the planner/clarification path instead of blindly executing ASR
            # garbage like "open heartshot".
            if resolve_open_target(app_name) is not None:
                return [Action(tool="open_application", args={"name": app_name}, risk="low", reason="Open app")]
            return []

        match = re.match(r"^(close|quit|kill)\s+(.+)$", normalized)
        if match:
            app_name = normalized.split(maxsplit=1)[1]
            return [
                Action(
                    tool="close_application",
                    args={"name": app_name, "force": False},
                    risk="medium",
                    reason="Close process by name",
                )
            ]

        if normalized.startswith("open url "):
            url = normalized[9:].strip()
            return [Action(tool="open_url", args={"url": url}, risk="low", reason="Open website")]

        if normalized.startswith("http://") or normalized.startswith("https://"):
            return [Action(tool="open_url", args={"url": normalized}, risk="low", reason="Open website")]

        if normalized.startswith("search web "):
            query = normalized[11:].strip()
            return [Action(tool="search_web", args={"query": query}, risk="low", reason="Open web search")]
        if normalized in {"research", "do research", "research this", "research that"}:
            return [Action(tool="research_agent_start", args={"question": normalized}, risk="low", reason="Start research agent workflow")]

        research_match = re.match(
            r"^(?:research|investigate|find reliable information about|look up|search the web for)\s+(.+)$",
            normalized,
        )
        if research_match:
            question = research_match.group(1).strip()
            if question:
                return [
                    Action(
                        tool="research_agent_start",
                        args={"question": question},
                        risk="low",
                        reason="Start research agent workflow",
                    )
                ]
        if normalized.startswith("read url "):
            url = normalized[9:].strip()
            return [Action(tool="read_webpage", args={"url": url}, risk="low", reason="Read webpage")]

        if normalized.startswith("recall "):
            return [Action(tool="memory_recall", args={"query": normalized[7:].strip()}, risk="low", reason="Recall relevant episodic memory")]
        if normalized.startswith("what did we do"):
            return [Action(tool="memory_list", args={"limit": 10}, risk="low", reason="List recent episodic memories")]
        if normalized in {"suggestions", "what can i do", "what can you do", "what should i do next"}:
            return [
                Action(
                    tool="suggest_next",
                    args={},
                    risk="low",
                    reason="Suggest next commands",
                )
            ]

        if normalized.startswith("search "):
            query = normalized[7:].strip()
            if query:
                return [Action(tool="search_web", args={"query": query}, risk="low", reason="Open web search")]

        if normalized.startswith("run "):
            shell_cmd = text[4:].strip()
            return [
                Action(
                    tool="run_shell_command",
                    args={"command": shell_cmd, "timeout_seconds": 30},
                    risk="high",
                    reason="Execute shell command",
                )
            ]

        if normalized.startswith("type "):
            typed_text = text[5:]
            return [Action(tool="type_text", args={"text": typed_text}, risk="medium", reason="Type text")]

        if normalized.startswith("press "):
            hotkey_text = normalized[6:].strip().replace("+", " ")
            keys = [key for key in hotkey_text.split() if key]
            if keys:
                return [Action(tool="press_hotkey", args={"keys": keys}, risk="medium", reason="Press shortcut")]

        if normalized.startswith("announce "):
            message = text[9:].strip()
            return [Action(tool="announce", args={"message": message}, risk="low", reason="Announce message")]

        remind_match = re.match(
            r"^remind me to (.+?)\s+(?:in (\d+) (minutes?|hours?)|at (\d{1,2}):(\d{2})|tomorrow at (\d{1,2}):(\d{2}))$",
            normalized,
        )
        if remind_match:
            message = remind_match.group(1).strip()
            if remind_match.group(2):
                when = f"in {remind_match.group(2)} {remind_match.group(3)}"
            elif remind_match.group(4):
                when = f"at {remind_match.group(4)}:{remind_match.group(5)}"
            else:
                when = f"tomorrow at {remind_match.group(6)}:{remind_match.group(7)}"
            return [
                Action(
                    tool="set_reminder",
                    args={"message": message, "when": when},
                    risk="low",
                    reason="Set reminder",
                )
            ]

        if normalized in {"suggestions", "what can i do", "what can you do", "what should i do next", "what s next", "suggest something"}:
            return [
                Action(
                    tool="suggest_next",
                    args={},
                    risk="low",
                    reason="Suggest next commands",
                )
            ]

        if normalized in {"idle time", "how long have i been idle", "how long idle", "am i idle"}:
            return [Action(tool="idle_time", args={}, risk="low", reason="Report idle time")]

        if "screenshot" in normalized:
            return [
                Action(
                    tool="capture_screenshot",
                    args={"output_path": "data/latest_screenshot.png"},
                    risk="low",
                    reason="Capture screen",
                )
            ]

        if normalized in {"look at the screen", "look at screen", "analyze screen", "analyze the screen"} or normalized in {
            "whats on my screen",
            "what s on my screen",
            "what's on my screen",
            "what is on my screen",
        }:
            return [
                Action(
                    tool="analyze_screen",
                    args={},
                    risk="low",
                    reason="Describe current screen",
                )
            ]

        if normalized in {"ocr screen", "read the screen", "read screen text", "read text on screen"}:
            return [
                Action(
                    tool="ocr_screen",
                    args={},
                    risk="low",
                    reason="Extract text from screen",
                )
            ]

        if normalized.startswith("list processes"):
            return [Action(tool="list_processes", args={"limit": 25}, risk="low", reason="Inspect running apps")]

        if normalized in {"system status", "status", "health check"}:
            return [Action(tool="system_status", args={}, risk="low", reason="Read system metrics")]

        if normalized in {
            "time",
            "what is the time",
            "what s the time",
            "current time",
            "tell me the time",
            "date and time",
        }:
            return [Action(tool="get_current_time", args={}, risk="low", reason="Read local time")]

        if normalized.startswith("find files "):
            pattern = text[11:].strip()
            return [Action(tool="find_files", args={"pattern": pattern, "limit": 50}, risk="low", reason="Search files")]

        match = re.match(r"^apply\s+for\s+(.+)$", normalized)
        if match:
            query = match.group(1).strip()
            return [Action(tool="job_apply", args={"query": query}, risk="high", reason="Apply for jobs matching the query")]

        if normalized in {"job status", "job progress", "application status", "application progress"}:
            return [Action(tool="job_status", args={}, risk="low", reason="Report job-application task status")]

        if normalized in {"job applied", "jobs applied", "applications", "applied jobs", "list applications"}:
            return [Action(tool="job_applied", args={"limit": 50}, risk="low", reason="List recent job applications")]

        if normalized.startswith("read file "):
            path = text[10:].strip()
            return [Action(tool="read_text_file", args={"path": path}, risk="low", reason="Read file content")]

        move_match = re.match(r"^move file (.+?) -> (.+)$", text, flags=re.IGNORECASE)
        if move_match:
            source_path = move_match.group(1).strip()
            target_path = move_match.group(2).strip()
            return [
                Action(
                    tool="move_file",
                    args={"source": source_path, "destination": target_path, "overwrite": False},
                    risk="medium",
                    reason="Move file",
                )
            ]

        return []
