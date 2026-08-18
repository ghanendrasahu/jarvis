from __future__ import annotations

from pathlib import Path

from app.agents.job_agent import JobAgent
from app.config import Settings
from app.core.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides) -> Settings:
    return Settings(
        workspace_root=str(ROOT),
        job_profile_path=str(ROOT / "config" / "job_profile.yaml"),
        job_site_profiles_path=str(ROOT / "config" / "site_profiles.yaml"),
        database_path=":memory:",
        **overrides,
    )


def _agent(**settings_overrides) -> JobAgent:
    settings = _settings(**settings_overrides)
    memory = MemoryStore(settings.database_path)
    return JobAgent(settings=settings, memory=memory, session=None)


def test_search_url_builds_with_query_and_location():
    agent = _agent()
    url = agent._build_search_url(agent._site_profiles["indeed"], "python developer")
    assert url.startswith("https://www.indeed.com/jobs")
    assert "q=python+developer" in url


def test_search_url_removes_remote_location_hint_when_remote_only():
    agent = _agent(job_apply_remote_only=True)
    url = agent._build_search_url(agent._site_profiles["linkedin"], "data engineer")
    assert url.startswith("https://www.linkedin.com/jobs/search")
    assert "remote" in url


def test_field_matching_uses_placeholder_and_label():
    agent = _agent()
    assert agent._match_field("email", {"placeholder": "Work email"})
    assert agent._match_field("phone", {"aria": "mobile number"})
    assert agent._match_field("full_name", {"label": "First & last name"})
    assert not agent._match_field("email", {"placeholder": "Password"})


def test_profile_value_resolves_from_preferences_and_identity():
    agent = _agent()
    assert agent._profile_value("salary_expectation") == "$90,000"
    assert agent._profile_value("email") == "you@example.com"
    assert agent._profile_value("summary")


def test_site_profiles_loaded_from_config():
    agent = _agent()
    assert "linkedin" in agent._site_profiles
    assert "indeed" in agent._site_profiles
    assert "generic" in agent._site_profiles
    for key in ("base_url", "results_selector", "card", "apply_button"):
        assert key in agent._site_profiles["linkedin"]


def test_job_application_logging_roundtrip():
    settings = _settings()
    memory = MemoryStore(settings.database_path)
    agent = JobAgent(settings=settings, memory=memory, session=None)
    agent.memory.log_job_application(
        site="indeed",
        title="Python Engineer",
        company="Acme",
        location="Remote",
        url="https://indeed.com/job/1",
        status="applied",
        notes="fields=4 resume=uploaded submit=yes",
    )
    assert agent.memory.is_job_applied("https://indeed.com/job/1")
    applied = agent.applied(limit=5)
    assert len(applied) == 1
    assert applied[0]["title"] == "Python Engineer"
    totals = agent.applied_totals()
    assert totals.get("applied") == 1
