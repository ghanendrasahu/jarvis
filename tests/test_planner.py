from types import SimpleNamespace

from app.llm.planner import _extract_json, _message_content
from app.models.schemas import Plan


def test_extract_json_plain():
    payload = _extract_json('{"summary": "x", "actions": []}')
    assert payload["summary"] == "x"


def test_extract_json_with_code_fence():
    payload = _extract_json('```json\n{"summary": "y"}\n```')
    assert payload["summary"] == "y"


def test_extract_json_with_reasoning_prose():
    text = "I will help. {\"summary\": \"z\", \"actions\": [{\"tool\": \"get_current_time\"}]} Hope that helps!"
    payload = _extract_json(text)
    assert payload["summary"] == "z"
    assert payload["actions"][0]["tool"] == "get_current_time"


def test_extract_json_returns_empty_on_garbage():
    assert _extract_json("no json here at all") == {}


def test_message_content_prefers_content_over_reasoning():
    message = SimpleNamespace(content="{\"summary\": \"real\"}", reasoning_content="{\"summary\": \"thinking\"}")
    assert _message_content(message) == "{\"summary\": \"real\"}"


def test_message_content_falls_back_to_reasoning():
    message = SimpleNamespace(content="", reasoning_content="{\"summary\": \"from reasoning\"}")
    assert _message_content(message) == "{\"summary\": \"from reasoning\"}"


def test_to_plan_survives_empty_actions():
    from app.llm.planner import _to_plan

    plan: Plan = _to_plan({"summary": "noop", "actions": None, "final_response": "Done"})
    assert plan.actions == []
    assert plan.final_response == "Done"


def test_create_planner_agent_tier_falls_back_to_main_provider():
    from app.config import Settings
    from app.llm.planner import OpenAIPlanner, create_planner

    settings = Settings(workspace_root=".", llm_provider="openai", openai_api_key="k")
    planner = create_planner(settings, for_agent=True)
    assert isinstance(planner, OpenAIPlanner)
    assert planner.model == settings.openai_model


def test_create_planner_agent_tier_uses_override():
    from app.config import Settings
    from app.llm.planner import GroqPlanner, create_planner

    settings = Settings(
        workspace_root=".",
        llm_provider="openai",
        openai_api_key="k",
        agent_llm_provider="groq",
        groq_api_key="g",
        agent_groq_model="groq/big-model",
    )
    planner = create_planner(settings, for_agent=True)
    assert isinstance(planner, GroqPlanner)
    assert planner.model == "groq/big-model"


def test_create_planner_litellm_missing_extra_returns_null():
    from app.config import Settings
    from app.llm.planner import NullPlanner, create_planner

    settings = Settings(workspace_root=".", llm_provider="litellm")
    planner = create_planner(settings)
    assert isinstance(planner, NullPlanner)
