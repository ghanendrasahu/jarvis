from __future__ import annotations

from app.agents.roles import (
    AGENT_ROLES,
    classify_intent,
    is_code_command,
    is_general_agent_command,
    role_for_id,
)


def test_is_code_command_preserves_legacy_behavior():
    assert is_code_command("fix the bug in scheduler")
    assert is_code_command("debug the failing test")
    assert is_code_command("refactor file_tools")
    assert is_code_command("write a function to parse dates")
    assert is_code_command("lint error in main")
    assert not is_code_command("open chrome")
    assert not is_code_command("system status")


def test_classify_knowledge():
    role = classify_intent("what do you know about my JobHunter project")
    assert role is not None
    assert role.id == "knowledge"
    remembered = classify_intent("remember that my wife's birthday is in June")
    assert remembered is not None
    assert remembered.id == "knowledge"


def test_classify_research():
    role = classify_intent("research the latest SBI recruitment")
    assert role is not None
    assert role.id == "research"
    searched = classify_intent("search the web for battery chargers")
    assert searched is not None
    assert searched.id == "research"


def test_classify_system():
    role = classify_intent("why is my laptop so slow")
    assert role is not None
    assert role.id == "system"
    diagnosed = classify_intent("diagnose the high ram usage")
    assert diagnosed is not None
    assert diagnosed.id == "system"


def test_classify_vision():
    role = classify_intent("why isn't this working on my screen")
    assert role is not None
    assert role.id == "vision"


def test_classify_productivity():
    role = classify_intent("plan my day")
    assert role is not None
    assert role.id == "productivity"
    forgot = classify_intent("what am I forgetting")
    assert forgot is not None
    assert forgot.id == "productivity"


def test_classify_monitoring():
    role = classify_intent("monitor my cpu usage")
    assert role is not None
    assert role.id == "monitoring"


def test_classify_returns_none_for_direct_commands_and_followups():
    assert classify_intent("open chrome") is None
    assert classify_intent("system status") is None
    assert classify_intent("what time is it") is None
    assert classify_intent("pause", {"active_surface": "youtube"}) is None


def test_general_agent_prefixes():
    assert is_general_agent_command("automate my file backup")
    assert is_general_agent_command("summarize my inbox")
    assert is_general_agent_command("check if the build passed")
    assert not is_general_agent_command("open chrome")


def test_role_for_id_and_uniqueness():
    assert role_for_id("coding") is not None
    ids = [role.id for role in AGENT_ROLES]
    assert len(ids) == len(set(ids)), "agent role ids must be unique"
    assert "coding" in ids
