from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.core import redact
from app.core.memory import MemoryStore
from app.core.orchestrator import JarvisOrchestrator
from app.core.router import DirectCommandRouter
from app.models.schemas import Action, Plan
from app.tools.registry import ToolRegistry


def test_register_secret_value_redacted():
    redact.register_secrets("sk-live-super-secret")
    try:
        text = "connect with sk-live-super-secret now"
        assert redact.PLACEHOLDER in redact.redact_text(text)
        assert "sk-live-super-secret" not in redact.redact_text(text)
    finally:
        redact._registered.discard("sk-live-super-secret")


def test_env_secret_redacted(monkeypatch):
    monkeypatch.setenv("TEST_STRIPE_API_KEY", "env-supersecret-42")
    assert redact.PLACEHOLDER in redact.redact_text("prefix env-supersecret-42 suffix")


def test_assignment_patterns_redacted():
    cases = [
        "api_key=sk-abcdef123456",
        "password: hunter2!",
        "Authorization: Bearer abc.def.ghi",
        "token = eyJhbGciOi",
        "key=value-secret",
    ]
    for case in cases:
        assert redact.PLACEHOLDER in redact.redact_text(case), case


def test_bearer_header_only_replaces_value():
    out = redact.redact_text("Authorization: Bearer mytoken123")
    assert "mytoken123" not in out
    assert redact.PLACEHOLDER in out


def test_bare_auth_and_auth_key_redacted():
    out = redact.redact_text("auth: Bearer tok-abc")
    assert "tok-abc" not in out
    assert redact.PLACEHOLDER in out
    redacted = redact.redact_object({"auth": "Bearer tok-abc"})
    assert redacted["auth"] == redact.PLACEHOLDER


def test_redact_object_deep():
    payload = {
        "result_text": "ok",
        "credentials": {"api_key": "abc123", "token": "xyz"},
        "headers": {"authorization": "Bearer live-token"},
        "nested": [{"password": "pw"}, "api_key=pw2"],
        "safe": "keep me",
    }
    out = redact.redact_object(payload)
    assert out["credentials"]["api_key"] == redact.PLACEHOLDER
    assert out["headers"]["authorization"] == redact.PLACEHOLDER
    assert out["nested"][0]["password"] == redact.PLACEHOLDER
    assert redact.PLACEHOLDER in out["nested"][1]
    assert out["safe"] == "keep me"
    assert out["result_text"] == "ok"


def test_memory_store_redacts_on_persist(tmp_path):
    memory = MemoryStore(str(tmp_path / "redact.db"))
    memory.log_command("cli", "echo secret", "result key=sk-abc12345 and password=hunter2")
    memory.log_tool_result(
        "echo secret",
        "run_shell_command",
        "completed",
        "high",
        output={"stdout": "api_key=sk-abc12345", "auth": "Bearer live-99999"},
        error="failed on token=deadbeef",
    )
    memory.set_state("runtime_context", {"token": "ctx-secret", "nested": {"password": "pw"}})
    memory.cache_set("c:k1", {"final_response": "key=cache-secret"}, max_entries=0)

    rows = memory.recent_conversation()
    assert rows
    assert "sk-abc12345" not in rows[0]["response_text"]
    assert redact.PLACEHOLDER in rows[0]["response_text"]

    tools = memory.recent_tool_history()
    assert tools
    payload = tools[0]
    assert "sk-abc12345" not in payload["output_json"]["stdout"]
    assert payload["output_json"]["auth"] == redact.PLACEHOLDER
    assert "deadbeef" not in (payload["error"] or "")
    assert redact.PLACEHOLDER in payload["error"]

    state = memory.get_state("runtime_context")
    assert state["token"] == redact.PLACEHOLDER
    assert state["nested"]["password"] == redact.PLACEHOLDER

    cached = memory.cache_get("c:k1")
    assert redact.PLACEHOLDER in cached["final_response"]


def _build_orchestrator(tmp_path) -> tuple[JarvisOrchestrator, MemoryStore, dict[str, Any]]:
    settings = Settings(workspace_root=".", database_path=str(tmp_path / "orch.db"))
    memory = MemoryStore(settings.database_path)

    def echo_secret(_: dict[str, Any]) -> dict[str, Any]:
        return {"result_text": "token=sk-orch-secret-77", "stdout": "Authorization: Bearer beartok99"}

    registry = ToolRegistry()
    registry.register("echo_secret", "echo a secret for tests", "low", {}, echo_secret)

    class FakePlanner:
        def plan(self, command, tool_catalog, history, agent_context="", tool_history=None, known_facts=None):
            return Plan(summary="leak", actions=[Action(tool="echo_secret", risk="low")], final_response="")

    from app.core.approvals import ApprovalManager

    approvals = ApprovalManager()
    orchestrator = JarvisOrchestrator(
        settings=settings,
        registry=registry,
        router=DirectCommandRouter(),
        planner=FakePlanner(),  # type: ignore[arg-type]
        executor=__import__("app.core.executor", fromlist=["ActionExecutor"]).ActionExecutor(
            registry, approvals, settings
        ),
        approvals=approvals,
        memory=memory,
    )
    return orchestrator, memory, {"result_text": "token=sk-orch-secret-77", "stdout": "Authorization: Bearer beartok99"}


def test_orchestrator_redacts_returned_payload_and_history(tmp_path):
    orchestrator, memory, _expected = _build_orchestrator(tmp_path)

    response = orchestrator.execute_command("leak check", source="cli", auto_approve=False)

    assert "sk-orch-secret-77" not in response.response_text
    assert redact.PLACEHOLDER in response.response_text
    assert "beartok99" not in response.response_text

    for item in response.results:
        assert "sk-orch-secret-77" not in json.dumps(item.output)
        assert "beartok99" not in json.dumps(item.output)
        assert item.output.get("stdout") is None or "beartok99" not in item.output["stdout"]

    rows = memory.recent_conversation()
    assert "sk-orch-secret-77" not in rows[0]["response_text"]
    tools = memory.recent_tool_history()
    assert tools
    assert "sk-orch-secret-77" not in json.dumps(tools[0]["output_json"])
    assert "beartok99" not in json.dumps(tools[0]["output_json"])


def test_shell_like_output_redacted(tmp_path):
    orchestrator, memory, _expected = _build_orchestrator(tmp_path)
    orchestrator.execute_command("leak check", source="cli", auto_approve=False)
    rows = memory.recent_tool_history()
    assert rows
    stdout = rows[0]["output_json"].get("stdout") or ""
    assert "sk-orch-secret-77" not in stdout
    assert redact.PLACEHOLDER in stdout or "sk-orch-secret-77" not in stdout
