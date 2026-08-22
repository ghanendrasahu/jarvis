from __future__ import annotations

import threading
import time
from typing import cast

from app.config import Settings
from app.core.orchestrator import JarvisOrchestrator
from app.core.router import DirectCommandRouter
from app.tools.system_tools import resolve_open_target
from app.voice.listener import VoiceListener
from app.voice.loop import VoiceController


class _DummyOrchestrator:
    def execute_command(self, command: str, source: str, auto_approve: bool = False):
        raise AssertionError("execute_command should not be called in these tests")


def test_youtube_open_and_play_routes_to_video_tool() -> None:
    router = DirectCommandRouter()
    actions = router.route("open YouTube and play daft punk harder better faster stronger")

    assert len(actions) == 1
    assert actions[0].tool == "open_youtube_video"
    assert actions[0].args["query"] == "daft punk harder better faster stronger"


def test_web_targets_resolve_to_browser_urls() -> None:
    assert resolve_open_target("GitHub") == {"kind": "url", "target": "https://github.com", "alias": "github"}
    assert resolve_open_target("gmail") == {"kind": "url", "target": "https://mail.google.com", "alias": "gmail"}


def test_desktop_targets_resolve_to_apps() -> None:
    assert resolve_open_target("chrome") == {"kind": "app", "target": "chrome.exe", "alias": "chrome"}
    assert resolve_open_target("vscode") == {"kind": "app", "target": "Code.exe", "alias": "vscode"}


def test_router_prefers_web_target_for_open_github() -> None:
    router = DirectCommandRouter()
    actions = router.route("open GitHub")

    assert len(actions) == 1
    assert actions[0].tool == "open_url"
    assert actions[0].args["url"] == "https://github.com"


def test_router_keeps_desktop_app_for_open_chrome() -> None:
    router = DirectCommandRouter()
    actions = router.route("open Chrome")

    assert len(actions) == 1
    assert actions[0].tool == "open_application"
    assert actions[0].args["name"] == "chrome"


def test_transcript_plausibility_rejects_garbage_and_accepts_real_phrase() -> None:
    assert not VoiceListener._is_transcript_plausible("thank", 2)
    assert not VoiceListener._is_transcript_plausible("xqz", 2)
    assert VoiceListener._is_transcript_plausible("open youtube", 2)


def test_voice_controller_start_stop_is_single_thread() -> None:
    class _Settings:
        groq_model = "test-model"
        wake_word = "jarvis"
        groq_api_key = ""
        openai_api_key = ""
        tts_enabled = False
        tts_engine = "sapi"
        kokoro_voice = "af_bella"
        kokoro_lang_code = "a"
        kokoro_speed = 1.0
        voice_min_transcript_chars = 2
        voice_followup_sticky_mode = True
        voice_followup_window_seconds = 10
        voice_confirm_before_send = False

    controller = VoiceController(
        cast(Settings, _Settings()),
        cast(JarvisOrchestrator, _DummyOrchestrator()),
    )

    entered = threading.Event()

    def _loop() -> None:
        entered.set()
        while not controller._stop_event.is_set():
            time.sleep(0.01)

    controller._run_loop = _loop  # type: ignore[method-assign]

    assert controller.start() is True
    assert entered.wait(timeout=1.0)
    assert controller.start() is False
    assert controller.is_running() is True
    assert controller.stop() is True
    assert controller.is_running() is False
