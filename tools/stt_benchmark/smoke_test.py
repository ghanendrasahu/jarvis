"""Smoke-test all backends with a known speech sample."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backends  # noqa: E402

WAV = Path(__file__).resolve().parent / (
    "models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/test_wavs/en.wav"
)


def main() -> None:
    for key in ("groq", "parakeet", "qwen06", "qwen17"):
        backend = backends.BUILDERS[key]()
        print(f"\n=== {backend.name} ===", flush=True)
        try:
            t0 = time.perf_counter()
            backend.load()
            print(f"load: {time.perf_counter() - t0:.1f}s", flush=True)
            t0 = time.perf_counter()
            text = backend.transcribe(WAV)
            print(f"transcribe: {time.perf_counter() - t0:.2f}s", flush=True)
            print(f"text: {text!r}")
            backend.unload()
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
