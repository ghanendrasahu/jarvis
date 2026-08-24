from __future__ import annotations

import pytest

from app.core.chat_router import classify_user_input, is_conversational


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # explicit commands
        ("open chrome", "command"),
        ("open https://example.com", "command"),
        ("close spotify", "command"),
        ("play blinding lights", "command"),
        ("youtube pause", "command"),
        ("search web python decorators", "command"),
        ("search for quantum computing", "command"),
        ("run dir", "command"),
        ("take screenshot", "command"),
        ("screenshot", "command"),
        ("system status", "command"),
        ("metrics", "command"),
        ("job status", "command"),
        ("apply for data scientist roles", "command"),
        ("remind me to stretch in 10 minutes", "command"),
        ("list apps", "command"),
        ("what time is it", "command"),
        ("what's on my screen", "command"),
        ("https://example.com", "command"),
        ("/cmd tell me a joke anyway", "command"),
        # conversational
        ("What is the capital of France?", "chat"),
        ("how are you today", "chat"),
        ("explain quantum computing briefly", "chat"),
        ("tell me a joke", "chat"),
        ("who are you", "chat"),
        ("can you write a haiku about GPUs", "chat"),
        ("why is the sky blue?", "chat"),
        ("summarize this article for me", "chat"),
        ("write a python function to reverse a string", "chat"),
        ("hello jarvis", "chat"),
        ("thanks, that helped!", "chat"),
        # ambiguous -> chat (never the heavy planner)
        ("quantum computing", "chat"),
        ("python decorators", "chat"),
    ],
)
def test_routing_table(text: str, expected: str) -> None:
    assert classify_user_input(text) == expected


def test_empty_input_is_conversational_noop() -> None:
    assert classify_user_input("") == "chat"
    assert is_conversational("   ") is True


def test_helper_matches_classifier() -> None:
    assert is_conversational("explain recursion") is True
    assert is_conversational("open notepad") is False


def test_force_command_prefix_strips_body() -> None:
    assert classify_user_input("/cmd hello there") == "command"
