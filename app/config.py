from __future__ import annotations

import os

# Suppress HuggingFace "unauthenticated requests" warning early
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

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
        populate_by_name=True,
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

    # LLM fallback chain: tried in order when primary provider fails (413/429/500).
    # Provider names: openai, groq, ollama, cerebras, openrouter, litellm
    llm_fallback_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    llm_max_retries: int = 2
    llm_retry_base_delay: float = 1.0
    llm_timeout_seconds: int = 30
    # Hard wall-clock budget for a full planner fallback chain (all providers+retries).
    llm_fallback_deadline_seconds: int = 180

    # Cerebras (free, 60K TPM — 7.5x more than Groq's free tier).
    cerebras_api_key: str | None = None
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_model: str = "gpt-oss-120b"

    # OpenRouter (free, 20+ models, single API key).
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemma-4-31b-it:free"

    # ------------------------------------------------------------- AI Assistant Chat
    # Dedicated conversational model layer (streaming). Fully env-driven; these
    # values control the live chat client (unlike the planner's fixed timeouts).
    # NOTE: field names intentionally mirror their environment variables
    # (pydantic-settings matches case-insensitively): jarvis_chat_provider
    # <-> JARVIS_CHAT_PROVIDER, etc.
    # Provider names: opencode|zen, groq, cerebras, openrouter, ollama.
    opencode_base_url: str = "https://opencode.ai/zen/v1"
    opencode_api_key: str | None = None
    jarvis_chat_provider: str = "opencode"
    jarvis_chat_model: str = ""  # empty -> provider default (opencode: nemotron-3-ultra-free)
    jarvis_chat_fallback_1: str = "zen:nemotron-3.5-lightning-free"
    jarvis_chat_fallback_2: str = "groq:llama-3.1-8b-instant"
    jarvis_chat_fallback_3: str = "cerebras:gpt-oss-120b"
    # Extra fallbacks appended after FALLBACK_1..3. Format: "provider:model".
    jarvis_chat_fallback_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    jarvis_chat_timeout_seconds: int = 30
    jarvis_chat_max_retries: int = 1
    # Hard wall-clock budget for the whole chain (all providers + retries).
    jarvis_chat_deadline_seconds: int = 90
    # Kept as strings so empty .env values ("") do not break int/float parsing.
    jarvis_chat_max_tokens: str | None = None
    jarvis_chat_temperature: str | None = None
    jarvis_chat_history_window: int = 12
    # Send attached images to the model (requires a vision-capable chat model).
    # When False, images still render in the GUI but are stripped from requests.
    jarvis_chat_vision_enabled: bool = False

    # Fast-planner result cache (repeat commands skip the LLM call entirely).
    llm_cache_enabled: bool = True
    llm_cache_ttl_days: int = 7
    llm_cache_max_entries: int = 500

    # Agent reasoning tier: empty provider = reuse the main LLM provider/model.
    agent_llm_provider: str = ""
    agent_openai_model: str = ""
    agent_groq_model: str = ""
    agent_ollama_model: str = ""
    agent_cerebras_model: str = ""
    agent_openrouter_model: str = ""
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
    # Sticky follow-up auto-expires after this many seconds; wake word required again.
    voice_followup_max_seconds: int = 120
    voice_confirm_before_send: bool = True
    voice_min_transcript_chars: int = 2
    speech_ambient_adjust_duration_seconds: float = 0.25
    speech_ambient_adjust_interval_seconds: int = 25

    # Voice understanding pipeline:
    # Whisper (hearing) -> fuzzy cleanup -> semantic LLM (understanding) -> router (deciding).
    stt_language: str = "en"
    stt_prompt_bias: bool = True
    voice_fuzzy_cutoff: int = 88
    voice_min_avg_logprob: float = -1.0
    voice_max_no_speech_prob: float = 0.9
    wake_word_fuzzy_enabled: bool = True
    wake_word_fuzzy_score: int = 82
    voice_semantic_enabled: bool = True
    voice_semantic_provider: str = ""  # empty = auto: groq, cerebras, openai, openrouter; or "ollama"
    voice_semantic_model: str = ""  # empty = provider default (groq/cerebras: llama-3.1-8b-instant)
    voice_semantic_timeout_seconds: int = 5
    voice_fuzzy_target_score: int = 75

    # STT backend chain. Primary "qwen3" runs the local Qwen3-ASR model
    # (lazy-loaded on first use from the shared HF cache); on failure it falls
    # back to "groq" (Whisper large-v3-turbo API). Set VOICE_STT_BACKEND=groq
    # to switch back to the previous behavior with one config change.
    voice_stt_backend: str = "qwen3"  # "qwen3" | "groq"
    voice_stt_fallback: str = "groq"  # tried when the primary fails/unavailable
    qwen_stt_model_id: str = "Qwen/Qwen3-ASR-0.6B-hf"
    # Voice-loop resilience (watchdog): auto-restart a crashed loop thread;
    # warn when the loop is silent longer than stale_seconds (cannot preempt).
    voice_watchdog_enabled: bool = True
    voice_watchdog_stale_seconds: int = 90
    voice_watchdog_max_restarts: int = 5
    # HUD watchdog: clear a stuck command/busy state after this many seconds.
    command_busy_timeout_seconds: int = 180

    # Kokoro-82M TTS engine (pip install -e .[tts] + espeak-ng system package).
    # Speaker mode: "sapi" | "pyttsx3" | "kokoro"
    tts_engine: str = "kokoro"
    kokoro_voice: str = "af_bella"
    kokoro_lang_code: str = "a"  # a=American English, b=British, j=Japanese, z=Chinese, etc.
    kokoro_speed: float = 1.0

    # Spoken + on-screen welcome greeting when the HUD starts.
    startup_greeting_enabled: bool = True

    approval_policy: str = "medium_and_high"
    auto_approve_low_risk: bool = True
    max_history_items: int = 20
    tool_retry_attempts: int = 1
    tool_retry_backoff_seconds: float = 0.4
    # Max concurrent tool workers that timed out but keep running in the
    # background; new executions fail fast while the cap is reached.
    executor_max_orphaned_workers: int = 16
    db_retention_days: int = 60

    # Browser automation toggle (set false to skip Playwright tools when unavailable).
    browser_enabled: bool = True

    # RAG / knowledge memory.
    rag_enabled: bool = False
    rag_index_path: str = "data/rag.db"
    rag_notes_dir: str = "data/notes"
    rag_embeddings_enabled: bool = False
    rag_embed_model: str = "nomic-embed-text"
    rag_top_k: int = 5
    rag_chunk_chars: int = 512
    rag_chunk_overlap_chars: int = 64

    # Local calendar
    calendar_path: str = "data/calendar.db"

    browser_profile_dir: str = "data/browser_profile"
    browser_automation_timeout_ms: int = 15000

    agent_max_iterations: int = 6
    agent_action_cap: int = 25
    agent_approval_policy: str = "none"

    # Coding agent: edit -> verify (tests + lint) -> fix loop.
    code_max_fix_rounds: int = 3
    code_verify_after_edit: bool = True

    # Vision (screen/image understanding): auto | openai | groq | ollama | litellm | disabled
    vision_provider: str = "auto"
    vision_model: str = ""

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

    @field_validator("allowed_shell_commands", "allowed_roots", "job_target_sites", "llm_fallback_providers", mode="before")
    @classmethod
    def _coerce_csv_lists(cls, value: Any) -> list[str]:
        return _parse_csv(value)

    @property
    def resolved_allowed_roots(self) -> list[Path]:
        # NOTE: the user's home directory is intentionally NOT an implicit
        # allowed root. Implicitly trusting the whole home tree would let the
        # agent read/write any file the user owns. Add explicit roots via
        # `allowed_roots` when broader access is required.
        roots = [Path(self.workspace_root).expanduser().resolve()]
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
        settings.cerebras_api_key,
        settings.openrouter_api_key,
        settings.litellm_api_key,
        settings.smtp_password,
    )
    return settings
