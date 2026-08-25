"""Live microphone smoke test through the production VoiceListener path.

Deterministic per-command flow: model pre-loads first, then each phrase gets a
countdown -> beep -> listen_once(). Exercises the real qwen3 -> groq chain.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

COMMANDS = [
    "open YouTube",
    "open Chrome",
    "open GitHub",
    "play music on YouTube",
    "skip this song",
]


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def beep(freq: int = 880, duration_ms: int = 220) -> None:
    try:
        import winsound

        winsound.Beep(freq, duration_ms)
    except Exception:
        print("\a", end="", flush=True)


def listen_with_prompt(listener, phrase: str):
    print(f"\n{'=' * 60}\nSAY:  \"{phrase}\"\n{'=' * 60}", flush=True)
    input("  Press ENTER, then speak after the beep > ")
    for remaining in range(2, 0, -1):
        print(f"  starting in {remaining}...", flush=True)
        time.sleep(1.0)
    beep(660, 150)
    beep(990, 250)
    print("  >>> SPEAK NOW <<<", flush=True)
    started = time.perf_counter()
    result = listener.listen_once()
    return result, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--commands", default="", help="semicolon-separated custom commands")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="route transcripts through the real JARVIS pipeline (opens apps!)",
    )
    args = parser.parse_args()

    from app.config import get_settings
    from app.voice.listener import ListenResult, VoiceListener

    settings = get_settings()
    listener = VoiceListener(settings)
    controller = None
    if args.execute:
        from app.bootstrap import build_runtime

        runtime = build_runtime()
        listener = runtime.voice.listener
        controller = runtime.voice
        print("EXECUTE MODE: recognized commands WILL run through the real router.")

    phrases = [c.strip() for c in args.commands.split(";") if c.strip()] or COMMANDS

    print(f"configured backend : {getattr(settings, 'voice_stt_backend', '?')}")
    print(f"fallback           : {getattr(settings, 'voice_stt_fallback', '?')}")
    print(f"model              : {getattr(settings, 'qwen_stt_model_id', '?')}")
    print("\nPre-loading Qwen3-ASR (one-time, ~60s)...", flush=True)
    t0 = time.perf_counter()
    listener._get_qwen()._ensure_loaded()
    print(f"Model ready in {time.perf_counter() - t0:.0f}s.\n", flush=True)

    rows: list[tuple[str, ListenResult]] = []
    for phrase in phrases:
        result, round_seconds = listen_with_prompt(listener, phrase)
        if result.issue in {"timeout", "no_speech"} and not result.transcript:
            print("  (not captured — retrying once)", flush=True)
            result, round_seconds = listen_with_prompt(listener, phrase)

        transcript = (result.transcript or "").strip()
        issue = result.issue or ""
        matched = normalize(transcript) == normalize(phrase) if transcript else False
        rows.append((phrase, result))
        flag = "EXACT-MATCH" if matched else (f"issue={issue}" if issue else "MISMATCH")
        stt_seconds = max(0.0, round_seconds - min(len(transcript.split()) / 2.5, 12.0))
        print(
            f"  [{listener.backend}] {transcript!r}\n"
            f"  round={round_seconds:.1f}s (~stt≈{stt_seconds:.1f}s) -> {flag}",
            flush=True,
        )

        if controller is not None and transcript:
            command, used_wake_word = controller._extract_command(transcript)
            if not command:
                print("  -> dropped by wake-word/follow-up gate (say 'jarvis <command>')", flush=True)
                continue
            canonical = controller._normalize_for_understanding(command)
            execution = controller._run_command(canonical)
            print(
                f"  -> command: {canonical!r} | status: {execution.status}\n"
                f"     response: {execution.response_text[:200]}",
                flush=True,
            )

    print("\n" + "=" * 60 + "\nSUMMARY\n" + "=" * 60)
    hits = 0
    for phrase, result in rows:
        transcript = (result.transcript or "").strip()
        ok = bool(transcript) and normalize(transcript) == normalize(phrase)
        hits += ok
        print(f"  {'OK ' if ok else 'MISS'} {phrase:<28} -> {transcript!r}")
    print(f"\nactive STT backend : {listener.backend}")
    print(f"exact matches      : {hits}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
