"""Normalize recording loudness so every model gets audible audio.

Peak-based: gain = target_peak / true_peak, so clipping cannot occur.
Creates recordings_boosted/ with the same manifest; originals stay untouched.
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parent / "recordings"
DST = Path(__file__).resolve().parent / "recordings_boosted"
TARGET_PEAK = 12000.0  # ~ -8.7 dBFS, comfortable headroom


def boost_file(src: Path, dst: Path) -> tuple[float, int]:
    with wave.open(str(src), "rb") as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)

    peak = int(np.max(np.abs(audio))) or 1
    gain = TARGET_PEAK / peak
    boosted = np.round(audio.astype(np.float64) * gain).astype(np.int16)

    with wave.open(str(dst), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(boosted.tobytes())
    return gain, peak


def main() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    out_manifest = []
    for entry in sorted(manifest, key=lambda e: e["id"]):
        gain, orig_peak = boost_file(SRC / entry["file"], DST / entry["file"])
        boosted = dict(entry)
        boosted["gain_applied"] = round(gain, 1)
        boosted["orig_peak"] = orig_peak
        out_manifest.append(boosted)
        print(f"#{entry['id']:02d} {entry['file']:<50} peak {orig_peak:>6} -> gain x{gain:.1f}")
    (DST / "manifest.json").write_text(json.dumps(out_manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(out_manifest)} peak-normalized files to {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
