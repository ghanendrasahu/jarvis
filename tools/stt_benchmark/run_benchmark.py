"""Run the STT benchmark: transcribe saved recordings with every model.

Usage:
    python run_benchmark.py                          # all models
    python run_benchmark.py --models groq,parakeet   # subset
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import threading
import time
from pathlib import Path

import psutil

reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(reconfigure):
    reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backends  # noqa: E402
import metrics  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = BASE_DIR / "recordings"
RESULTS_PATH = BASE_DIR / "results.jsonl"


def load_manifest() -> list[dict]:
    entries = json.loads((RECORDINGS_DIR / "manifest.json").read_text(encoding="utf-8"))
    return sorted(entries, key=lambda e: e["id"])


class RamMonitor:
    def __init__(self) -> None:
        self._process = psutil.Process()
        self._peak = self._process.memory_info().rss
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self._process.memory_info().rss
                self._peak = max(self._peak, rss)
            except Exception:
                pass
            self._stop.wait(0.05)

    def stop_and_peak_mb(self, baseline_rss: int) -> float:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return max(0.0, (self._peak - baseline_rss)) / (1024 * 1024)


def human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n / (1024 * 1024):.0f} MB" if n < (1024**3) else f"{n / (1024**3):.1f} GB"


def fmt_latency(seconds: float) -> str:
    return f"{seconds:.1f}s"


def run_model(key: str, backend: backends.Backend, recordings: list[dict]) -> dict:
    baseline_rss = psutil.Process().memory_info().rss
    print(f"\n--- Loading {backend.name} ---", flush=True)
    t0 = time.perf_counter()
    backend.load()
    load_time = time.perf_counter() - t0
    monitor = RamMonitor()
    monitor.start()

    warm = recordings[0]
    try:
        backend.transcribe(RECORDINGS_DIR / warm["file"])
    except Exception as exc:
        print(f"  warmup failed: {exc}", flush=True)

    rows: list[dict] = []
    for entry in recordings:
        wav = RECORDINGS_DIR / entry["file"]
        error: str | None = None
        transcript = ""
        started = time.perf_counter()
        try:
            transcript = backend.transcribe(wav)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency = time.perf_counter() - started

        row = {
            "model": backend.name,
            "command_id": entry["id"],
            "expected": entry["phrase"],
            "transcript": transcript,
            "latency_s": round(latency, 3),
            "error": error,
        }
        row.update(metrics.score(entry["phrase"], transcript))
        rows.append(row)
        with open(RESULTS_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

        label = error or transcript or "(empty)"
        flag = "" if row.get("exact_match") else "   << mismatch"
        print(f"  #{entry['id']:02d} [{fmt_latency(latency)}] {label}{flag}", flush=True)

    peak_extra_mb = monitor.stop_and_peak_mb(baseline_rss)
    backend.unload()
    gc.collect()
    time.sleep(1.0)

    valid = [r for r in rows if r["error"] is None]
    failures = sum(1 for r in rows if r["error"] is not None or not r["transcript"].strip())
    return {
        "rows": rows,
        "summary": {
            "name": backend.name,
            "key": key,
            "avg_accuracy": round(sum(r["accuracy"] for r in rows) / len(rows), 4),
            "exact_matches": sum(1 for r in rows if r.get("exact_match")),
            "total": len(rows),
            "failures": failures,
            "avg_latency": round(sum(r["latency_s"] for r in valid) / len(valid), 3) if valid else -1,
            "disk": backend.disk_bytes(),
            "peak_ram_mb": round(peak_extra_mb),
            "load_time": round(load_time, 1),
        },
    }


def print_block(entry: dict, results_by_model: dict[str, list[dict]]) -> None:
    print(f"\nCommand #{entry['id']}")
    print(f"Expected: {entry['phrase']}\n")
    for model_name, rows in results_by_model.items():
        row = next((r for r in rows if r["command_id"] == entry["id"]), None)
        if row is None:
            continue
        print(f"{model_name}:")
        body = row["error"] or row["transcript"] or "(empty)"
        print(f"    {body}")
        print(f"    latency: {fmt_latency(row['latency_s'])}")


def main() -> int:
    global RECORDINGS_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        default="groq,qwen06,qwen17,parakeet",
        help=f"comma-separated subset of {sorted(backends.BUILDERS)}",
    )
    parser.add_argument(
        "--dir", default=str(RECORDINGS_DIR), help="recordings directory with manifest.json"
    )
    args = parser.parse_args()

    RECORDINGS_DIR = Path(args.dir)

    recordings = load_manifest()
    if not recordings:
        print("No recordings found. Run record.py first.")
        return 1

    keys = [k.strip() for k in args.models.split(",") if k.strip()]
    unknown = [k for k in keys if k not in backends.BUILDERS]
    if unknown:
        print(f"Unknown model(s): {unknown}. Valid: {sorted(backends.BUILDERS)}")
        return 1

    RESULTS_PATH.write_text("", encoding="utf-8")
    stats: list[dict] = []
    per_model_rows: dict[str, list[dict]] = {}
    try:
        for key in keys:
            backend = backends.BUILDERS[key]()
            per_model_rows[backend.name] = []
            try:
                result = run_model(key, backend, recordings)
                stats.append(result["summary"])
                per_model_rows[backend.name] = result["rows"]
                with open(RESULTS_PATH, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"summary": result["summary"]}) + "\n")
            except Exception as exc:
                print(f"\n!!! {key} could not run on this machine: {exc}", flush=True)
                stats.append({
                    "name": backend.name, "key": key, "avg_accuracy": 0.0,
                    "exact_matches": 0, "total": len(recordings), "failures": len(recordings),
                    "avg_latency": -1, "disk": None, "peak_ram_mb": 0, "load_time": 0,
                    "unavailable": True,
                })
                with open(RESULTS_PATH, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"unavailable": key, "reason": str(exc)}) + "\n")
    except KeyboardInterrupt:
        print("\nInterrupted — partial results kept in", RESULTS_PATH)

    for entry in recordings:
        print_block(entry, per_model_rows)

    print_summary(stats)
    return 0


def print_summary(stats: list[dict]) -> None:
    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    header = f"{'Model':<38} {'Acc':>6} {'Exact':>6} {'Lat':>7} {'Fail':>5} {'Size':>9} {'RAM':>9}"
    print(header)
    print("-" * len(header))
    for s in sorted(stats, key=lambda x: x["avg_accuracy"], reverse=True):
        if s.get("unavailable"):
            print(f"{s['name']:<38} {'UNAVAILABLE ON THIS MACHINE':<40}")
            continue
        lat = fmt_latency(s["avg_latency"]) if s["avg_latency"] >= 0 else "?"
        print(
            f"{s['name']:<38} {s['avg_accuracy']:>6.1%} "
            f"{str(s['exact_matches']) + '/' + str(s['total']):>6} {lat:>7} "
            f"{s['failures']:>5} {human_bytes(s['disk']):>9} {str(s['peak_ram_mb']) + ' MB':>9}"
        )

    available = [s for s in stats if not s.get("unavailable")]
    if not available:
        print("\nNo usable models.")
        return
    winner = sorted(
        available, key=lambda s: (-s["avg_accuracy"], s["avg_latency"], s["peak_ram_mb"])
    )[0]
    print(f"\nBEST FOR JARVIS: {winner['name']}")


if __name__ == "__main__":
    sys.exit(main())
