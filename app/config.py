from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "JARVIS"
    environment: str = "development"
    workspace_root: str = str(Path.cwd())
    data_dir: str = "data"
    logs_dir: str = "logs"
    database_path: str = "data/jarvis.db"

    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    wake_word: str = "jarvis"
    voice_enabled: bool = True
    tts_enabled: bool = True
    speech_timeout_seconds: int = 8
    phrase_time_limit_seconds: int = 12
    max_voice_response_chars: int = 260
    voice_followup_window_seconds: int = 10
    voice_followup_sticky_mode: bool = True
    voice_confirm_before_send: bool = True
    voice_min_transcript_chars: int = 2
    speech_ambient_adjust_duration_seconds: float = 0.25
    speech_ambient_adjust_interval_seconds: int = 25

    api_token: str | None = None
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    approval_policy: str = "medium_and_high"
    auto_approve_low_risk: bool = True
    max_history_items: int = 40
    tool_retry_attempts: int = 1
    tool_retry_backoff_seconds: float = 0.4
    db_retention_days: int = 60

    browser_profile_dir: str = "data/browser_profile"
    browser_automation_timeout_ms: int = 15000

    agent_max_iterations: int = 6
    agent_action_cap: int = 25
    agent_approval_policy: str = "none"

    job_profile_path: str = "config/job_profile.yaml"
    job_site_profiles_path: str = "config/site_profiles.yaml"
    resume_pdf_path: str = "data/resume/CV.pdf"
    job_target_sites: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["linkedin", "indeed"])
    job_max_apply_per_run: int = 5
    job_apply_remote_only: bool = False
    job_use_burner_account: bool = True

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    allowed_shell_commands: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "get-date",
            "get-process",
            "get-service",
            "get-childitem",
            "tasklist",
            "ipconfig",
            "ping",
            "where",
            "dir",
            "git",
            "python",
            "pip",
        ]
    )
    allowed_roots: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("allowed_shell_commands", "allowed_roots", "cors_allowed_origins", "job_target_sites", mode="before")
    @classmethod
    def _coerce_csv_lists(cls, value: Any) -> list[str]:
        return _parse_csv(value)

    @property
    def resolved_allowed_roots(self) -> list[Path]:
        roots = [Path(self.workspace_root).expanduser().resolve(), Path.home().resolve()]
        roots.extend(Path(item).expanduser().resolve() for item in self.allowed_roots)
        deduped: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(root)
        return deduped

    def ensure_runtime_dirs(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.logs_dir).mkdir(parents=True, exist_ok=True)
        db_parent = Path(self.database_path).expanduser().resolve().parent
        db_parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    from app.core.logging import setup_logging

    setup_logging(settings.logs_dir)
    return settings
