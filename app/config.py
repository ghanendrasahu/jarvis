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
    groq_model: str = "openai/gpt-oss-120b"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # Fast-planner result cache (repeat commands skip the LLM call entirely).
    llm_cache_enabled: bool = True
    llm_cache_ttl_days: int = 7
    llm_cache_max_entries: int = 500

    # Agent reasoning tier: empty provider = reuse the main LLM provider/model.
    agent_llm_provider: str = ""
    agent_openai_model: str = ""
    agent_groq_model: str = ""
    agent_ollama_model: str = ""
    agent_litellm_model: str = ""

    # LiteLLM gateway (optional extra: pip install -e .[litellm]).
    litellm_model: str = "groq/llama-3.3-70b-versatile"
    litellm_api_base: str | None = None
    litellm_api_key: str | None = None

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

    approval_policy: str = "medium_and_high"
    auto_approve_low_risk: bool = True
    max_history_items: int = 40
    tool_retry_attempts: int = 1
    tool_retry_backoff_seconds: float = 0.4
    db_retention_days: int = 60

    # RAG / knowledge memory.
    rag_enabled: bool = True
    rag_index_path: str = "data/rag.db"
    rag_notes_dir: str = "data/notes"
    rag_embeddings_enabled: bool = True
    rag_embed_model: str = "nomic-embed-text"
    rag_top_k: int = 5
    rag_chunk_chars: int = 512
    rag_chunk_overlap_chars: int = 64

    browser_profile_dir: str = "data/browser_profile"
    browser_automation_timeout_ms: int = 15000

    agent_max_iterations: int = 6
    agent_action_cap: int = 25
    agent_approval_policy: str = "none"

    # Coding agent: edit -> verify (tests + lint) -> fix loop.
    code_max_fix_rounds: int = 3
    code_verify_after_edit: bool = True

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

    @field_validator("allowed_shell_commands", "allowed_roots", "job_target_sites", mode="before")
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

    from app.core import redact

    redact.register_secrets(
        settings.openai_api_key,
        settings.groq_api_key,
        settings.litellm_api_key,
        settings.smtp_password,
    )
    return settings
