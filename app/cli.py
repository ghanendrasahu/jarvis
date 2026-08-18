from __future__ import annotations

import argparse
import json

from app.bootstrap import build_runtime


def run_console(enable_voice: bool = False) -> None:
    runtime = build_runtime()

    if enable_voice and runtime.settings.voice_enabled:
        runtime.voice.start()
        print("[voice] listening started")

    print("JARVIS CLI online. Type commands or /help.")
    while True:
        try:
            user_input = input("jarvis> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue

        normalized = " ".join(user_input.lower().split())
        if normalized in {"/quit", "/exit"}:
            break
        if normalized == "/help":
            print(
                "/voice start | /voice stop | /voice status | /voice ping | /voice test | /voice probe | /voice devices | /voice use <index> | /voice use default | /voice outputs | /voice output <index> | /voice output default | /voice mode <pyttsx3|sapi> | /pending | /approve <id> | /reject <id> | /metrics [limit] | /quit"
            )
            continue
        if normalized == "/pending":
            pending = runtime.orchestrator.list_pending_approvals()
            print(json.dumps([item.model_dump() for item in pending], indent=2))
            continue
        if normalized.startswith("id:"):
            approval_id = user_input.split(":", 1)[1].strip()
            if not approval_id:
                print("missing approval id after 'id:'")
                continue
            approval = runtime.orchestrator.approve(approval_id)
            print(json.dumps(approval.model_dump(), indent=2))
            continue
        if normalized.startswith("/approve "):
            approval_id = user_input.split(maxsplit=1)[1].strip()
            approval = runtime.orchestrator.approve(approval_id)
            print(json.dumps(approval.model_dump(), indent=2))
            continue
        if normalized.startswith("/reject "):
            approval_id = user_input.split(maxsplit=1)[1].strip()
            approval = runtime.orchestrator.reject(approval_id)
            print(json.dumps(approval.model_dump(), indent=2))
            continue
        if normalized.startswith("/metrics"):
            parts = user_input.split()
            limit = 200
            if len(parts) > 1:
                try:
                    limit = int(parts[1])
                except ValueError:
                    print("invalid limit, using default 200")
            print(json.dumps(runtime.orchestrator.get_metrics(limit=limit), indent=2))
            continue
        if normalized == "/voice start":
            if runtime.voice.start():
                print(f"started (wake word: {runtime.settings.wake_word})")
                print("say: jarvis system status")
            else:
                print("already running")
            continue
        if normalized == "/voice stop":
            if runtime.voice.stop():
                print("stopped")
            else:
                print("still stopping (or already stopped). check /voice status")
            continue
        if normalized == "/voice status":
            print(json.dumps(runtime.voice.status(), indent=2))
            continue
        if normalized == "/voice ping":
            if runtime.voice.is_running():
                runtime.voice.speaker.speak("Voice channel check. I am online.")
                print("ping sent to speaker")
            else:
                print("voice is not running")
            continue
        if normalized == "/voice test":
            runtime.voice.speaker.speak("This is a direct speaker test from Jarvis.")
            print("speaker test sent")
            continue
        if normalized == "/voice probe":
            if runtime.voice.is_running():
                print("stop live listener first with /voice stop")
                continue
            print("listening once for up to a few seconds. speak now.")
            print(json.dumps(runtime.voice.probe_once(), indent=2))
            continue
        if normalized == "/voice devices":
            print(json.dumps(runtime.voice.list_input_devices(), indent=2))
            continue
        if normalized.startswith("/voice use "):
            raw = user_input.split(maxsplit=2)[2].strip().lower()
            if raw == "default":
                print(json.dumps(runtime.voice.set_input_device(None), indent=2))
                continue
            try:
                index = int(raw)
                print(json.dumps(runtime.voice.set_input_device(index), indent=2))
            except ValueError as exc:
                print(f"invalid device index: {exc}")
            continue
        if normalized == "/voice outputs":
            print(json.dumps(runtime.voice.list_output_devices(), indent=2))
            continue
        if normalized.startswith("/voice output "):
            raw = user_input.split(maxsplit=2)[2].strip().lower()
            if raw == "default":
                print(json.dumps(runtime.voice.set_output_device(None), indent=2))
                continue
            try:
                index = int(raw)
                print(json.dumps(runtime.voice.set_output_device(index), indent=2))
            except ValueError as exc:
                print(f"invalid output index: {exc}")
            continue
        if normalized.startswith("/voice mode "):
            mode = user_input.split(maxsplit=2)[2].strip().lower()
            try:
                print(json.dumps(runtime.voice.set_speaker_mode(mode), indent=2))
            except ValueError as exc:
                print(f"invalid voice mode: {exc}")
            continue

        result = runtime.orchestrator.execute_command(command=user_input, source="cli", auto_approve=False)
        print(json.dumps(result.model_dump(), indent=2))

    runtime.voice.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JARVIS CLI assistant.")
    parser.add_argument("--voice", action="store_true", help="Start voice loop with CLI.")
    args = parser.parse_args()
    run_console(enable_voice=args.voice)


if __name__ == "__main__":
    main()
