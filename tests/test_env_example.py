"""Guard against .env.example drift: B7 (encoding) and B8 (key/value parity).

pydantic-settings reads .env with encoding="utf-8" (app/config.py), so the
example must be valid UTF-8 without a BOM, and every key/value must mirror
the Settings model.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from app.config import Settings

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / ".env.example"


def test_env_example_is_strict_utf8_without_bom() -> None:
    raw = EXAMPLE_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM found"
    assert not raw.startswith(b"\xff\xfe"), "UTF-16 LE BOM found"
    assert b"\x00" not in raw, "NUL bytes suggest UTF-16 encoding"
    raw.decode("utf-8", errors="strict")


def test_env_example_keys_match_settings_model_exactly() -> None:
    values = dotenv_values(EXAMPLE_PATH, encoding="utf-8")
    keys = [key for key in values if key is not None]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicate keys in .env.example: {duplicates}"

    known = {name.upper() for name in Settings.model_fields}
    unknown = sorted(set(keys) - known)
    missing = sorted(known - set(keys))
    assert not unknown, f"stale keys not in Settings: {unknown}"
    assert not missing, f"Settings fields missing from .env.example: {missing}"


def test_env_example_parses_with_project_defaults(monkeypatch) -> None:
    """The example must load under the project's own env-file semantics."""
    for name in (
        "LLM_PROVIDER",
        "TTS_ENGINE",
        "RAG_ENABLED",
        "RAG_EMBEDDINGS_ENABLED",
        "MAX_HISTORY_ITEMS",
        "JARVIS_CHAT_PROVIDER",
        "JARVIS_CHAT_MODEL",
        "JOB_PROFILE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=EXAMPLE_PATH, _env_file_encoding="utf-8")  # type: ignore[call-arg]
    assert settings.llm_provider == "openai"
    assert settings.tts_engine == "kokoro"
    assert settings.rag_enabled is False
    assert settings.rag_embeddings_enabled is False
    assert settings.max_history_items == 20
    assert settings.jarvis_chat_provider == "opencode"
    assert settings.jarvis_chat_model == ""
    assert settings.job_profile_path == "config/job_profile.yaml"


# --------------------------------------------------------------------------- B11-followup: CSV list fields


def test_chat_fallback_providers_env_values_parse_like_sibling_fields(
    monkeypatch,
) -> None:
    """Empty and comma-separated values must not crash Settings (startup bug)."""
    for name in ("JARVIS_CHAT_FALLBACK_PROVIDERS", "LLM_FALLBACK_PROVIDERS"):
        monkeypatch.delenv(name, raising=False)

    empty = Settings(llm_provider="groq", llm_cache_enabled=False, jarvis_chat_fallback_providers="")  # type: ignore[arg-type]
    assert empty.jarvis_chat_fallback_providers == []

    csv = Settings(llm_provider="groq", llm_cache_enabled=False, jarvis_chat_fallback_providers="groq:m9,cerebras:m3")  # type: ignore[arg-type]
    assert csv.jarvis_chat_fallback_providers == ["groq:m9", "cerebras:m3"]


def test_extra_chat_fallback_providers_extend_the_service_chain() -> None:
    from app.llm.chat import ChatService

    settings = Settings(
        llm_provider="groq",
        llm_cache_enabled=False,
        jarvis_chat_fallback_providers="ollama:llama3.1:8b",  # type: ignore[arg-type]
    )
    chain = [spec.name for spec in ChatService(settings).chain]

    assert chain[0] == "opencode"
    assert chain[-1] == "ollama", "extra fallback providers must be appended to the chain"
    assert len(chain) == len(set(chain)), "duplicate provider names are de-duplicated"
