from __future__ import annotations

import base64
from pathlib import Path

import requests

from app.config import Settings

DEFAULT_DESCRIBE_PROMPT = "Describe what you see in this image in detail, including any text."
DEFAULT_OCR_PROMPT = "Extract all text visible in this image. Return the text verbatim, preserving layout order."

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
