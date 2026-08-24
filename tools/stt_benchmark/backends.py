"""STT backend adapters. Each backend loads once, transcribes many WAVs."""
from __future__ import annotations

import gc
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
MODELS_DIR = BASE_DIR / "models"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = REPO_ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class Backend(ABC):
    name: str = "base"

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def transcribe(self, wav_path: Path) -> str: ...

    def unload(self) -> None:
        return None

    def disk_bytes(self) -> int | None:
        return None


class GroqBackend(Backend):
    """Baseline: same Groq endpoint JARVIS uses, but raw (no prompt/language bias)."""

    def __init__(self, language: str | None = None, label: str | None = None) -> None:
        self.name = label or "Whisper Turbo (Groq API)"
        self.language = language
        self._client: Any = None

    def load(self) -> None:
        from openai import OpenAI

        env = load_env()
        api_key = env.get("GROQ_API_KEY")
        base_url = env.get("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
        if not api_key:
            raise RuntimeError("GROQ_API_KEY missing from .env")
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=1)

    def transcribe(self, wav_path: Path) -> str:
        kwargs: dict = {}
        if self.language:
            kwargs["language"] = self.language
        with open(wav_path, "rb") as handle:
            result = self._client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=handle,
                **kwargs,
            )
        return (result.text or "").strip()

    def disk_bytes(self) -> int | None:
        return None


class QwenBackend(Backend):
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.size = model_id.split("-ASR-")[1].removesuffix("-hf")
        self.name = f"Qwen3-ASR {self.size} (CPU fp32)"
        self._model: Any = None
        self._processor: Any = None

    def load(self) -> None:
        import torch
        from transformers import AutoProcessor, Qwen3ASRForConditionalGeneration

        torch.set_num_threads(4)
        self._processor = AutoProcessor.from_pretrained(self.model_id)
        self._model = Qwen3ASRForConditionalGeneration.from_pretrained(
            self.model_id, dtype=torch.float32
        )
        self._model.eval()

    def transcribe(self, wav_path: Path) -> str:
        import torch

        inputs = self._processor.apply_transcription_request(
            audio=str(wav_path), language="English"
        ).to(self._model.device)
        with torch.inference_mode():
            output_ids = self._model.generate(**inputs, do_sample=False, max_new_tokens=256)
        generated = output_ids[:, inputs["input_ids"].shape[1]:]
        return self._decode(generated).strip()

    def _decode(self, generated) -> str:
        try:
            text = self._processor.decode(generated, return_format="transcription_only")
            if isinstance(text, (list, tuple)):
                text = text[0]
            if isinstance(text, str):
                return text
        except Exception:
            pass
        return self._processor.batch_decode(generated, skip_special_tokens=True)[0]

    def unload(self) -> None:
        self._model = None
        self._processor = None
        gc.collect()

    def disk_bytes(self) -> int | None:
        try:
            from huggingface_hub import scan_cache_dir

            for repo in scan_cache_dir().repos:
                if repo.repo_id == self.model_id:
                    return repo.size_on_disk
        except Exception:
            pass
        return None


class ParakeetBackend(Backend):
    def __init__(self) -> None:
        self.name = "Parakeet TDT 0.6B v3 (ONNX int8)"
        self._model: Any = None

    def load(self) -> None:
        import onnx_asr

        self._model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")

    def transcribe(self, wav_path: Path) -> str:
        return str(self._model.recognize(str(wav_path))).strip()

    def unload(self) -> None:
        self._model = None
        gc.collect()

    def disk_bytes(self) -> int | None:
        try:
            from huggingface_hub import scan_cache_dir

            for repo in scan_cache_dir().repos:
                if repo.repo_id == "istupakov/parakeet-tdt-0.6b-v3-onnx":
                    return repo.size_on_disk
        except Exception:
            pass
        return None


BUILDERS: dict[str, Callable[[], Backend]] = {
    "groq": GroqBackend,
    "groq_en": lambda: GroqBackend(language="en", label="Whisper Turbo (Groq, lang=en)"),
    "qwen06": lambda: QwenBackend("Qwen/Qwen3-ASR-0.6B-hf"),
    "qwen17": lambda: QwenBackend("Qwen/Qwen3-ASR-1.7B-hf"),
    "parakeet": ParakeetBackend,
}
