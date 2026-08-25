"""Record benchmark phrases from the microphone.

Same basic audio format as JARVIS: 16 kHz, 16-bit, mono PCM WAV.
Usage:
    python record.py                     # interactive session (presets + custom)
    python record.py --phrase "hello"    # single phrase
    python record.py --list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import wave
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = BASE_DIR / "recordings"
MANIFEST_PATH = RECORDINGS_DIR / "manifest.json"
SAMPLE_RATE = 16000

PRESET_PHRASES = [
    "open YouTube",
    "open Chrome",
    "open GitHub",
    "open Spotify",
    "play music on YouTube",
    "skip this song",
    "search the web for Python tutorials",
]


def beep(freq: int = 880, duration_ms: int = 220) -> None:
    try:
        import winsound

        winsound.Beep(freq, duration_ms)
    except Exception:
        print("\a", end="", flush=True)


def load_manifest() -> list[dict]:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return []


def save_manifest(entries: list[dict]) -> None:
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def slugify(phrase: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    return slug[:40] or "phrase"


def capture(max_seconds: float, silence_seconds: float, threshold: float = 200.0):
    import numpy as np
    import sounddevice as sd

    block = int(SAMPLE_RATE * 0.05)
    frames: list = []
    speech_started = False
    last_voice_at: float | None = None
    peak = 0.0

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=block)
    stream.start()
    start = time.perf_counter()
    try:
        while True:
            elapsed = time.perf_counter() - start
            if elapsed >= max_seconds:
                break
            data, _ = stream.read(block)
            mono = data[:, 0]
            frames.append(mono.copy())
            level = float(np.sqrt(np.mean(mono.astype(np.float32) ** 2)))
            peak = max(peak, level)
            if level >= threshold:
                speech_started = True
                last_voice_at = time.perf_counter()
            elif speech_started and last_voice_at is not None and time.perf_counter() - last_voice_at >= silence_seconds:
                break
    finally:
        stream.stop()
        stream.close()

    audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)
    return audio, speech_started, peak


def save_wav(path: Path, audio) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(audio.tobytes())


def record_phrase(
    phrase: str,
    max_seconds: float,
    silence_seconds: float,
    delay: float,
    replace_id: int | None = None,
    threshold: float = 200.0,
) -> dict:
    entries = load_manifest()
    entry_id = replace_id if replace_id is not None else len(entries) + 1
    filename = f"{entry_id:02d}_{slugify(phrase)}.wav"

    print(f"\n{'=' * 60}")
    label = f"REDO #{entry_id:02d}" if replace_id is not None else "SAY"
    print(f'{label}:  "{phrase}"')
    print(f"{'=' * 60}")
    input("  Press ENTER, then speak after the beep > ")
    for remaining in range(int(delay), 0, -1):
        print(f"  starting in {remaining}...", flush=True)
        time.sleep(1.0)
    beep(660, 150)
    beep(990, 250)
    print("  >>> RECORDING — speak now <<<", flush=True)

    audio, speech_started, peak = capture(max_seconds, silence_seconds, threshold)
    seconds = len(audio) / SAMPLE_RATE
    path = RECORDINGS_DIR / filename
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    save_wav(path, audio)

    entry = {
        "id": entry_id,
        "phrase": phrase,
        "file": filename,
        "seconds": round(seconds, 2),
        "peak_rms": round(peak, 1),
        "speech_detected": bool(speech_started),
    }
    if replace_id is not None:
        entries = [e for e in entries if e["id"] != replace_id]
    entries.append(entry)
    entries.sort(key=lambda e: e["id"])
    save_manifest(entries)

    status = "OK" if speech_started else "WARNING: no speech detected — consider re-recording"
    print(f"  saved {filename} ({seconds:.1f}s, peak {peak:.0f}) [{status}]", flush=True)
    beep(440, 150)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phrase", help="record a single phrase and exit")
    parser.add_argument("--max", type=float, default=8.0, help="max seconds per recording")
    parser.add_argument("--silence", type=float, default=1.2, help="trailing silence to stop")
    parser.add_argument("--delay", type=float, default=1.5, help="countdown after ENTER")
    parser.add_argument("--threshold", type=float, default=200.0, help="speech RMS threshold")
    parser.add_argument(
        "--redo", action="store_true", help="re-record only takes with no detected speech"
    )
    parser.add_argument("--list", action="store_true", help="show manifest and exit")
    args = parser.parse_args()

    if args.list:
        for entry in load_manifest():
            flag = "" if entry.get("speech_detected") else " [no-speech]"
            print(f"#{entry['id']:02d}  {entry['phrase']}  ({entry['seconds']}s){flag}")
        return 0

    if args.phrase:
        record_phrase(args.phrase.strip(), args.max, args.silence, args.delay)
        return 0

    if args.redo:
        bad = [e for e in load_manifest() if not e.get("speech_detected")]
        if not bad:
            print("All recordings look good — nothing to redo.")
            return 0
        print(f"{len(bad)} take(s) need re-recording.")
        for entry in bad:
            record_phrase(
                entry["phrase"], args.max, args.silence, args.delay, replace_id=entry["id"]
            )
        return 0

    print("STT Benchmark Recorder — speak naturally, like talking to JARVIS.")
    input("Press ENTER when ready... ")
    for phrase in PRESET_PHRASES:
        record_phrase(phrase, args.max, args.silence, args.delay)

    while True:
        answer = input("\nAdd a custom phrase? [y/N] ").strip().lower()
        if answer != "y":
            break
        custom = input("Phrase: ").strip()
        if custom:
            record_phrase(custom, args.max, args.silence, args.delay)

    total = len(load_manifest())
    print(f"\nDone. {total} recordings in {RECORDINGS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
