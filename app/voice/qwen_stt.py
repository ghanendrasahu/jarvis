"""Local Qwen3-ASR STT backend.

Lazy-loaded on the voice worker thread at first use; the process keeps a single
cached instance. Model weights come from the shared Hugging Face cache (the
same files the tools/stt_benchmark evaluation downloaded) - no duplicate copy.
"""
from __future__ import annotations

import os
import tempfile
import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger("voice")

DEFAULT_QWEN_STT_MODEL_ID = "Qwen/Qwen3-ASR-0.6B-hf"

# Qwen3-ASR expects full language names; map JARVIS ISO codes onto them.
_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "zh": "Chinese",
    "ja": "Japanese",
}


class Qwen3AsrTranscriber:
    """Thread-safe lazy wrapper around a locally cached Qwen3-ASR model."""

    def __init__(self, model_id: str = DEFAULT_QWEN_STT_MODEL_ID) -> None:
        self._model_id = model_id or DEFAULT_QWEN_STT_MODEL_ID
        self._lock = threading.Lock()
        self._model: Any = None
        self._processor: Any = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self) -> None:
        if self.loaded:
            return
        with self._lock:
            if self.loaded:
                return
            import torch
            try:
                from transformers import AutoProcessor, Qwen3ASRForConditionalGeneration
            except ImportError as exc:
                # transformers<5.13 ships no Qwen3-ASR classes; fail with actionable guidance
                # instead of a bare ImportError deep inside the voice worker thread.
                raise RuntimeError(
                    "transformers>=5.13 is required for the Qwen3-ASR STT backend "
                    f"(missing Qwen3ASRForConditionalGeneration: {exc}). "
                    'Fix: pip install -U "transformers>=5.13" (or pip install -e ".[stt-qwen]")'
                ) from exc

            torch.set_num_threads(min(4, os.cpu_count() or 1))
            logger.info("[stt:qwen3] loading %s (first use)...", self._model_id)

            # P1-7: Check if model files are already cached locally.
            # If so, use local_files_only=True to skip HF metadata requests
            # (the repeated GET /api/model endpoints that slow down startup).
            try:
                from huggingface_hub import try_to_load_from_cache

                cache_status = try_to_load_from_cache(
                    self._model_id, "config.json",
                )
                local_only = cache_status is not None and not isinstance(cache_status, Exception)
            except Exception:
                local_only = False

            load_kwargs: dict[str, Any] = {}
            if local_only:
                load_kwargs["local_files_only"] = True
                logger.info("[stt:qwen3] model cached locally, skipping HF metadata")

            processor = AutoProcessor.from_pretrained(self._model_id, **load_kwargs)
            model = Qwen3ASRForConditionalGeneration.from_pretrained(
                self._model_id, dtype=torch.float32, **load_kwargs,
            )
            model.eval()
            self._processor = processor
            self._model = model
            logger.info("[stt:qwen3] model ready on CPU (fp32)")

    def transcribe_wav_bytes(self, wav_bytes: bytes, language_code: str = "") -> str:
        """Transcribe 16 kHz 16-bit mono PCM WAV bytes; returns raw text."""
        self._ensure_loaded()
        import torch

        # generate() is not thread-safe; serialize concurrent callers (probe API).
        with self._lock:
            path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                    handle.write(wav_bytes)
                    path = handle.name

                kwargs: dict[str, Any] = {}
                language = _LANGUAGE_NAMES.get((language_code or "").strip().lower())
                if language:
                    kwargs["language"] = language

                inputs = self._processor.apply_transcription_request(audio=path, **kwargs).to(
                    self._model.device
                )
                with torch.inference_mode():
                    output_ids = self._model.generate(
                        **inputs, do_sample=False, max_new_tokens=256
                    )
                generated = output_ids[:, inputs["input_ids"].shape[1]:]
                return self._decode(generated).strip()
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

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
