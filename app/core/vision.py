from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import requests

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger("vision_analyzer")

DEFAULT_DESCRIBE_PROMPT = "Describe what you see in this image in detail, including any text."
DEFAULT_OCR_PROMPT = "Extract all text visible in this image. Return the text verbatim, preserving layout order."
DEFAULT_WINDOW_DETECT_PROMPT = (
    "Identify the active application/window in this screenshot. "
    "Return the application name, window title, and any visible UI elements. "
    "Format as JSON: {\"app\": \"...\", \"title\": \"...\", \"elements\": [\"...\"]}"
)
DEFAULT_ERROR_LOOKUP_PROMPT = (
    "Analyze this screenshot for error messages, stack traces, or failure indicators. "
    "Return JSON: {\"errors\": [{\"type\": \"...\", \"message\": \"...\", \"severity\": \"low|medium|high\"}], "
    "\"suggestions\": [\"...\"]}"
)

_IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",
    b"BM": "bmp",
}

_MODEL_DEFAULTS = {
    "openai": "gpt-4o-mini",
    "groq": "llama-3.2-11b-vision-preview",
    "ollama": "llava",
    "litellm": "groq/llama-3.3-70b-versatile",
}

# Known error patterns for quick detection without LLM
_KNOWN_ERROR_PATTERNS = [
    (re.compile(r"(?i)connection (refused|failed|timeout|reset)"), "connection_error", "Connection failed"),
    (re.compile(r"(?i)segmentation fault|segfault"), "segfault", "Segmentation fault"),
    (re.compile(r"(?i)out of memory|oom killed|memory error"), "memory_error", "Out of memory"),
    (re.compile(r"(?i)permission denied|access denied"), "permission_error", "Permission denied"),
    (re.compile(r"(?i)file not found|no such file"), "file_not_found", "File not found"),
    (re.compile(r"(?i)command not found|not recognized as an internal"), "command_not_found", "Command not found"),
    (re.compile(r"(?i)syntax error|invalid syntax"), "syntax_error", "Syntax error"),
    (re.compile(r"(?i)traceback \(most recent call last\)"), "python_traceback", "Python exception"),
    (re.compile(r"(?i)exception in thread|thread.*exception"), "thread_exception", "Thread exception"),
    (re.compile(r"(?i)error:.*build failed|build error"), "build_error", "Build failed"),
    (re.compile(r"(?i)npm ERR!|yarn error|pip install.*error"), "dependency_error", "Dependency error"),
    (re.compile(r"(?i)timeout|timed out"), "timeout", "Operation timed out"),
    (re.compile(r"(?i)disk full|no space left"), "disk_full", "Disk full"),
    (re.compile(r"(?i)authentication failed|unauthorized|401|403"), "auth_error", "Authentication failed"),
    (re.compile(r"(?i)certificate verify failed|ssl.*error"), "ssl_error", "SSL/TLS error"),
    (re.compile(r"(?i)deadlock|race condition"), "concurrency_error", "Concurrency issue"),
    (re.compile(r"(?i)null pointer|nil pointer|undefined.*variable"), "null_pointer", "Null/undefined reference"),
    (re.compile(r"(?i)index out of bounds|array index out of range"), "index_error", "Index out of bounds"),
    (re.compile(r"(?i)division by zero|divide by zero"), "division_error", "Division by zero"),
    (re.compile(r"(?i)stack overflow|maximum recursion"), "stack_overflow", "Stack overflow"),
    (re.compile(r"(?i)assertion failed|assert.*false"), "assertion_error", "Assertion failed"),
]


def detect_known_errors(text: str) -> list[dict[str, str]]:
    """Detect known error patterns in text using regex."""
    detected = []
    for pattern, error_type, description in _KNOWN_ERROR_PATTERNS:
        if pattern.search(text):
            detected.append({
                "type": error_type,
                "message": description,
                "severity": "high" if error_type in {"segfault", "memory_error", "disk_full", "stack_overflow"} else "medium"
            })
    return detected


def detect_image_format(path: Path) -> str | None:
    try:
        with path.open("rb") as file_handle:
            head = file_handle.read(16)
    except OSError:
        return None
    for signature, fmt in _IMAGE_SIGNATURES.items():
        if head.startswith(signature):
            return fmt
    return None


class VisionAnalyzer:
    """Sends an image plus a prompt to a vision-capable chat model.

    Provider selection: ``vision_provider`` (auto | openai | groq | ollama |
    litellm | disabled). ``auto`` mirrors the main LLM provider where a vision
    model is available, else falls back to local Ollama (llava).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        provider = (settings.vision_provider or "auto").strip().lower()
        self.provider = self._resolve_provider(provider)

    def _resolve_provider(self, provider: str) -> str:
        if provider == "disabled":
            return "disabled"
        if provider != "auto":
            return provider
        llm = self.settings.llm_provider.lower().strip()
        if llm in {"openai", "groq", "litellm"}:
            return llm
        return "ollama"

    def model(self) -> str:
        return self.settings.vision_model.strip() or _MODEL_DEFAULTS.get(self.provider, _MODEL_DEFAULTS["ollama"])

    def available(self) -> tuple[bool, str]:
        provider = self.provider
        if provider == "disabled":
            return False, "Vision is disabled (vision_provider=disabled)."
        if provider == "openai":
            if not self.settings.openai_api_key:
                return False, "OPENAI_API_KEY is not set."
            return True, ""
        if provider == "groq":
            if not self.settings.groq_api_key:
                return False, "GROQ_API_KEY is not set."
            return True, ""
        if provider == "litellm":
            if not (self.settings.litellm_api_base or self.settings.litellm_api_key):
                return False, "LiteLLM vision gateway is not configured."
            return True, ""
        if provider == "ollama":
            return True, ""
        return False, f"Unsupported vision_provider: {provider}"

    def analyze(self, image_path: str, prompt: str = DEFAULT_DESCRIBE_PROMPT) -> str:
        ok, reason = self.available()
        if not ok:
            raise RuntimeError(f"Vision unavailable: {reason}")
        path = Path(image_path).expanduser()
        fmt = detect_image_format(path)
        if fmt is None:
            raise ValueError(f"Not a supported image file: {path}")
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")

        if self.provider == "ollama":
            return self._chat_ollama(image_b64, prompt)
        if self.provider in {"openai", "groq", "litellm"}:
            return self._chat_openai_compatible(image_b64, prompt)
        raise RuntimeError(f"Vision unavailable: unsupported provider {self.provider}")

    def _chat_ollama(self, image_b64: str, prompt: str) -> str:
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": self.model(),
                "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
                "stream": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        return ((response.json().get("message") or {}).get("content") or "").strip()

    def _chat_openai_compatible(self, image_b64: str, prompt: str) -> str:
        endpoint, api_key = self._endpoint_and_key()
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model(),
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                            },
                        ],
                    }
                ],
                "max_tokens": 700,
            },
            timeout=60,
        )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            raise RuntimeError("Vision model returned no response.")
        return (choices[0].get("message") or {}).get("content") or ""

    def _endpoint_and_key(self) -> tuple[str, str]:
        settings = self.settings
        if self.provider == "openai":
            return "https://api.openai.com/v1/chat/completions", settings.openai_api_key or ""
        if self.provider == "groq":
            return f"{settings.groq_base_url.rstrip('/')}/chat/completions", settings.groq_api_key or ""
        if self.provider == "litellm":
            base = (settings.litellm_api_base or "http://localhost:4000").rstrip("/")
            return f"{base}/chat/completions", settings.litellm_api_key or ""
        raise RuntimeError(f"Unsupported provider: {self.provider}")

    def detect_window(self, image_path: str) -> dict[str, Any]:
        """Detect the active window/application in a screenshot."""
        return self._analyze_with_prompt(image_path, DEFAULT_WINDOW_DETECT_PROMPT, "window detection")

    def detect_errors(self, image_path: str) -> dict[str, Any]:
        """Detect error messages and failure indicators in a screenshot."""
        return self._analyze_with_prompt(image_path, DEFAULT_ERROR_LOOKUP_PROMPT, "error detection")

    def analyze_with_context(self, image_path: str, prompt: str = DEFAULT_DESCRIBE_PROMPT) -> dict[str, Any]:
        """Full analysis pipeline: describe + window detection + error detection + reasoning."""
        path = Path(image_path).expanduser()
        fmt = detect_image_format(path)
        if fmt is None:
            raise ValueError(f"Not a supported image file: {path}")

        # Step 1: General description
        description = self.analyze(image_path, prompt)

        # Step 2: Window/app detection
        window_info = self.detect_window(image_path)

        # Step 3: Error detection via LLM
        error_info = self.detect_errors(image_path)

        # Step 4: Quick regex-based error detection on description
        known_errors = detect_known_errors(description)

        # Combine all errors
        all_errors = error_info.get("errors", [])
        for ke in known_errors:
            if not any(ke["type"] == e.get("type") for e in all_errors):
                all_errors.append(ke)

        # Step 5: Generate reasoning and suggestions
        suggestions = self._generate_reasoning(description, window_info, all_errors)
        error_info.get("suggestions", []).extend(suggestions)

        return {
            "description": description,
            "window": window_info,
            "errors": all_errors,
            "suggestions": error_info.get("suggestions", []),
            "has_errors": len(all_errors) > 0,
            "result_text": self._format_result(description, window_info, all_errors),
        }

    def _analyze_with_prompt(self, image_path: str, prompt: str, context: str) -> dict[str, Any]:
        """Run vision analysis with a structured prompt."""
        try:
            result = self.analyze(image_path, prompt)
            # Try to parse JSON from result
            json_match = re.search(r"\{.*\}", result, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            # Fallback: return raw result
            return {"raw": result, "context": context}
        except Exception as e:
            logger.warning("%s failed: %s", context, e)
            return {"error": str(e), "context": context}

    def _generate_reasoning(self, description: str, window_info: dict, errors: list[dict]) -> list[str]:
        """Generate reasoning-based suggestions based on analysis."""
        suggestions = []

        if errors:
            for error in errors:
                error_type = error.get("type", "")
                if error_type == "connection_error":
                    suggestions.append("Check network connectivity and firewall settings")
                    suggestions.append("Verify the service/endpoint is reachable")
                elif error_type == "memory_error":
                    suggestions.append("Free up memory or increase available RAM")
                    suggestions.append("Check for memory leaks in the application")
                elif error_type == "permission_error":
                    suggestions.append("Run with appropriate permissions (admin/root if needed)")
                    suggestions.append("Check file/folder permissions")
                elif error_type == "file_not_found":
                    suggestions.append("Verify the file path exists and is correct")
                    suggestions.append("Check if the file was moved or deleted")
                elif error_type == "command_not_found":
                    suggestions.append("Install the missing command/tool")
                    suggestions.append("Check PATH environment variable")
                elif error_type == "python_traceback":
                    suggestions.append("Examine the full traceback for root cause")
                    suggestions.append("Check the line number mentioned in the error")
                elif error_type == "build_error":
                    suggestions.append("Check build logs for detailed error messages")
                    suggestions.append("Verify dependencies are installed correctly")
                elif error_type == "dependency_error":
                    suggestions.append("Run dependency installation (npm install, pip install, etc.)")
                    suggestions.append("Check package versions for compatibility")
                elif error_type == "auth_error":
                    suggestions.append("Verify API keys and credentials")
                    suggestions.append("Check token expiration and refresh if needed")
                elif error_type == "ssl_error":
                    suggestions.append("Update certificates or check system time")
                    suggestions.append("Verify SSL/TLS configuration")
                elif error_type == "disk_full":
                    suggestions.append("Free up disk space")
                    suggestions.append("Clean temporary files and logs")
                else:
                    suggestions.append(f"Investigate {error_type}: {error.get('message', '')}")

        # App-specific suggestions
        window_app = window_info.get("app", "").lower()
        if "terminal" in window_app or "cmd" in window_app or "powershell" in window_app:
            suggestions.append("Terminal detected - consider running diagnostic commands")
        elif "browser" in window_app or "chrome" in window_app or "firefox" in window_app or "edge" in window_app:
            suggestions.append("Browser detected - check developer console for errors")
        elif "code" in window_app or "vscode" in window_app or "editor" in window_app:
            suggestions.append("Code editor detected - check linting and type checking")
        elif "docker" in window_app or "container" in window_app:
            suggestions.append("Container environment detected - check container logs")

        return suggestions

    def _format_result(self, description: str, window_info: dict, errors: list[dict]) -> str:
        """Format analysis result for display."""
        parts = [f"Screen: {description[:200]}..."]
        if window_info.get("app"):
            parts.append(f"App: {window_info['app']}")
        if errors:
            parts.append(f"Errors: {len(errors)} detected")
            for e in errors[:3]:
                parts.append(f"  - {e.get('type', 'unknown')}: {e.get('message', '')}")
        return " | ".join(parts)
