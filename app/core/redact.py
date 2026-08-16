from __future__ import annotations

import os
import re
import threading
from typing import Any

PLACEHOLDER = "<REDACTED>"

_SENSITIVE_NAME = re.compile(
    r"(api[_-]?key|password|passwd|pwd|token|secret|cookie|authorization|credential|bearer|access[_-]?key|\bkey\b|\bauth\b)",
    re.IGNORECASE,
)

_ASSIGNMENT = re.compile(
    r"(?P<key>(?:api[_-]?key|password|passwd|pwd|token|secret|cookie|authorization|credential|bearer|access[_-]?key|\bkey\b|\bauth\b))"
    r"\s*[=:]\s*[\"']?(?P<value>[^\"'\s,;|&]+)",
    re.IGNORECASE,
)

_BEARER_HEADER = re.compile(r"(?i)(authorization|auth)\s*:\s*bearer\s+([a-z0-9._~+/=-]+)")

_lock = threading.Lock()
_registered: set[str] = set()


def register_secrets(*values: str | None) -> None:
    """Register known secret values (e.g. from Settings) so they are always redacted."""
    with _lock:
        for value in values:
            clean = (value or "").strip()
            if len(clean) >= 4:
                _registered.add(clean)


def _env_secrets() -> set[str]:
    found: set[str] = set()
    for name, value in os.environ.items():
        if _SENSITIVE_NAME.search(name) and value and len(value.strip()) >= 4:
            found.add(value.strip())
    return found


def redact_text(text: Any) -> str:
    """Replace sensitive values in a string with a placeholder.

    Redacts any registered/env secret value plus common ``key=value`` / bearer patterns.
    """
    if text is None:
        return ""
    result = str(text)
    if not result:
        return result
    with _lock:
        known = set(_registered)
    secrets = known | _env_secrets()
    for secret in secrets:
        if secret in result:
            result = result.replace(secret, PLACEHOLDER)
    result = _BEARER_HEADER.sub(lambda match: f"{match.group(1)}: {PLACEHOLDER}", result)
    result = _ASSIGNMENT.sub(lambda match: f"{match.group('key')}={PLACEHOLDER}", result)
    return result


def redact_object(obj: Any) -> Any:
    """Deep-redact a data structure (dicts, lists, strings) before persistence or display."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _SENSITIVE_NAME.search(key):
                out[key] = redact_object(value) if isinstance(value, (dict, list, tuple)) else PLACEHOLDER
            else:
                out[key] = redact_object(value)
        return out
    if isinstance(obj, list):
        return [redact_object(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_object(item) for item in obj)
    return obj
