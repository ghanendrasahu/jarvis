from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from app.config import Settings
from app.core.nlu import (
    build_lexicon,
    build_stt_prompt,
    correct_transcript,
    fuzzy_wake_detected,
    needs_semantic_interpretation,
    strip_filler,
)
from app.core.orchestrator import JarvisOrchestrator
from app.tools.system_tools import APP_ALIASES, WEB_APP_ALIASES
from app.voice.listener import VoiceListener
from app.voice.loop import VoiceController
from app.voice.semantic import (
    SemanticInterpreter,
    _extract_json,
    build_capability_digest,
    canonicalize_intent,
)
from app.voice.wake_word_detector import PhraseMatchingDetectorForIntegration


def test_lexicon_is_built_from_existing_registries() -> None:
    lexicon = build_lexicon()
    assert "chrome" in lexicon.apps
    assert set(APP_ALIASES).issubset(lexicon.apps)
    assert "whatsapp" in lexicon.websites
    assert set(WEB_APP_ALIASES).issubset(lexicon.websites)
    assert {"youtube", "whatsapp"} <= lexicon.surfaces
    assert "play" in lexicon.verbs


def test_stt_prompt_contains_real_vocabulary() -> None:
    prompt = build_stt_prompt("jarvis")
    assert "jarvis" in prompt
    assert "youtube" in prompt
    assert "whatsapp" in prompt
    assert len(prompt) < 1000


def test_strip_filler_removes_politeness_and_wake_prefix() -> None:
    assert strip_filler("hey jarvis could you open whatsapp please") == "open whatsapp"
    assert strip_filler("could you pause youtube") == "pause youtube"
    assert strip_filler("system status") == "system status"


def test_correct_transcript_fixes_single_edit_typos_only() -> None:
    corrected, changed = correct_transcript("play on youtub")
    assert changed
    assert "youtube" in corrected

    corrected, changed = correct_transcript("open whatsap")
    assert changed
    assert "whatsapp" in corrected

    untouched, changed = correct_transcript("search web lofi beats playlist")
    assert not changed
    assert untouched == "search web lofi beats playlist"

    preserved, _changed = correct_transcript("Open VSCode")
    assert preserved.startswith("Open")


def test_needs_semantic_interpretation_flags_mangled_commands_not_queries() -> None:
    assert needs_semantic_interpretation("open hot tub")
    assert needs_semantic_interpretation("open whatsap")
    assert not needs_semantic_interpretation("search web hot tub restaurants near me")
    assert not needs_semantic_interpretation("play lofi beats on youtube")
    assert not needs_semantic_interpretation("system status")
    assert not needs_semantic_interpretation("youtube pause")
    assert not needs_semantic_interpretation("")
    assert not needs_semantic_interpretation("what do you think about quantum physics tomorrow")


def test_fuzzy_wake_word_accepts_variants_rejects_unrelated_words() -> None:
    assert fuzzy_wake_detected("hey jarvis play music", "jarvis")
    assert fuzzy_wake_detected("hey jervis play music", "jarvis")
    assert fuzzy_wake_detected("okay jarvic stop", "jarvis")
    assert not fuzzy_wake_detected("the service station is closed", "jarvis")
    assert not fuzzy_wake_detected("play music", "jarvis")

    strict = fuzzy_wake_detected("hey jervis play music", "jarvis", threshold=95.0)
    assert not strict


def test_wake_word_detector_fuzzy_toggle() -> None:
    detector = PhraseMatchingDetectorForIntegration("jarvis")
    assert detector.detect_transcript("Hey Jarvis, what's the time")
    assert detector.detect_transcript("hey jervis open chrome")
    assert not detector.detect_transcript("open chrome without waking")

    exact_only = PhraseMatchingDetectorForIntegration("jarvis", fuzzy_enabled=False)
    assert exact_only.detect_transcript("hey jarvis open chrome")
    assert not exact_only.detect_transcript("hey jervis open chrome")


def test_canonicalize_intent_maps_onto_existing_router_grammar() -> None:
    lexicon = build_lexicon()

    assert canonicalize_intent({"intent": "open_app", "target": "WhatsApp"}, lexicon) == "open whatsapp"
    assert canonicalize_intent({"intent": "open_website", "target": "YouTube"}, lexicon) == "open youtube"
    assert canonicalize_intent({"intent": "close_app", "target": "Chrome"}, lexicon) == "close chrome"
    assert canonicalize_intent({"intent": "youtube_play", "query": "some jazz"}, lexicon) == "youtube play some jazz"
    assert canonicalize_intent({"intent": "youtube_control", "action": "pause"}, lexicon) == "youtube pause"
    assert (
        canonicalize_intent({"intent": "youtube_control", "action": "skip_forward", "amount": 30}, lexicon)
        == "youtube skip 30"
    )
    assert (
        canonicalize_intent({"intent": "youtube_control", "action": "skip_back", "amount": 15}, lexicon)
        == "youtube rewind 15"
    )
    assert (
        canonicalize_intent({"intent": "search_web", "query": "hot tub restaurants near me"}, lexicon)
        == "search web hot tub restaurants near me"
    )
    assert (
        canonicalize_intent({"intent": "send_whatsapp", "contact": "12345", "message": "hi there"}, lexicon)
        == "send whatsapp to 12345: hi there"
    )
    assert canonicalize_intent({"intent": "system_info", "variant": "time"}, lexicon) == "time"
    assert canonicalize_intent({"intent": "system_info", "variant": "date"}, lexicon) == "date and time"
    assert canonicalize_intent({"intent": "system_info", "variant": "status"}, lexicon) == "system status"


def test_canonicalize_intent_never_invents_targets() -> None:
    lexicon = build_lexicon()
    assert canonicalize_intent({"intent": "open_app", "target": "flying carpet machine"}, lexicon) is None
    assert canonicalize_intent({"intent": "open_app"}, lexicon) is None
    assert canonicalize_intent({"intent": "unknown"}, lexicon) is None
    assert canonicalize_intent({"intent": "tell_joke"}, lexicon) is None
    assert canonicalize_intent({"intent": "search_web"}, lexicon) is None
    assert canonicalize_intent({}, lexicon) is None


def test_canonicalize_intent_accepts_learned_apps() -> None:
    lexicon = build_lexicon()
    result = canonicalize_intent(
        {"intent": "open_app", "target": "spotify"},
        lexicon,
        extra_apps={"spotify"},
    )
    assert result == "open spotify"


def test_capability_digest_reflects_live_registries() -> None:
    digest = build_capability_digest(
        build_lexicon(),
        tool_names=("capture_screenshot", "get_current_time"),
        frequent_apps={"spotify": 4},
    )
    assert "chrome" in digest
    assert "gmail" in digest
    assert "youtube" in digest
    assert "spotify" in digest
    assert "capture_screenshot" in digest


def test_semantic_json_extraction_is_defensive() -> None:
    assert _extract_json('{"intent": "unknown"}') == {"intent": "unknown"}
    assert _extract_json('```json\n{"intent": "open_app"}\n```') == {"intent": "open_app"}
    assert _extract_json('Sure! {"intent": "close_app"} hope that helps') == {"intent": "close_app"}
    assert _extract_json("not json at all") == {}
    assert _extract_json("") == {}


def test_listener_confidence_parsing_from_verbose_segments() -> None:
    verbose = SimpleNamespace(
        text="open whatsapp",
        segments=[
            {"avg_logprob": -0.3, "no_speech_prob": 0.1, "start": 0.0, "end": 2.0},
            {"avg_logprob": -0.9, "no_speech_prob": 0.4, "start": 2.0, "end": 3.0},
            {"avg_logprob": None, "no_speech_prob": 0.9, "start": 3.0, "end": 4.0},
        ],
    )
    confidence = VoiceListener._confidence_from_verbose(verbose)
    assert confidence is not None
    avg_logprob, max_no_speech = confidence
    assert abs(avg_logprob - (-0.5)) < 0.01
    assert abs(max_no_speech - 0.4) < 0.01

    assert VoiceListener._confidence_from_verbose(SimpleNamespace(text="", segments=[])) is None


class _DummyOrchestrator:
    def execute_command(self, command: str, source: str, auto_approve: bool = False):
        raise AssertionError("execute_command should not be called in these tests")


def _controller_without_providers() -> VoiceController:
    settings = SimpleNamespace(
        groq_model="test-model",
        wake_word="jarvis",
        groq_api_key="",
        cerebras_api_key="",
        openai_api_key="",
        openrouter_api_key="",
        tts_enabled=False,
        tts_engine="sapi",
        kokoro_voice="af_bella",
        kokoro_lang_code="a",
        kokoro_speed=1.0,
        voice_min_transcript_chars=2,
        voice_followup_sticky_mode=True,
        voice_followup_window_seconds=10,
        voice_confirm_before_send=False,
    )
    return VoiceController(
        cast(Settings, settings),
        cast(JarvisOrchestrator, _DummyOrchestrator()),
    )


def test_normalize_fast_path_needs_no_llm() -> None:
    controller = _controller_without_providers()

    assert controller._normalize_for_understanding("could you pause youtube") == "pause youtube"
    assert controller._normalize_for_understanding("hey jarvis system status") == "system status"


def test_normalize_corrects_obvious_typos_without_llm() -> None:
    controller = _controller_without_providers()
    assert controller._normalize_for_understanding("open whatsap") == "open whatsapp"


def test_normalize_preserves_original_when_semantic_cannot_rescue() -> None:
    controller = _controller_without_providers()
    # No provider keys configured: interpreter unavailable, original flows to planner as before.
    assert controller._normalize_for_understanding("open hot tub") == "open hot tub"


def test_interpreter_returns_none_without_any_provider() -> None:
    interpreter = SemanticInterpreter(
        cast(
            Settings,
            SimpleNamespace(
                groq_api_key="",
                cerebras_api_key="",
                openai_api_key="",
                openrouter_api_key="",
                voice_semantic_provider="",
                voice_semantic_model="",
                voice_semantic_timeout_seconds=5,
                voice_fuzzy_target_score=75,
            ),
        )
    )
    assert interpreter.interpret("open hot tub") is None
