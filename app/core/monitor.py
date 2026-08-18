from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass
class ResourceSample:
    cpu_percent: float
    memory_percent: float
    net_sent_bytes: int
    net_recv_bytes: int
    net_sent_kbps: float
    net_recv_kbps: float


class ResourceMonitor:
    """Sampled psutil snapshot suitable for GUI resource panels."""

    def __init__(self, history_size: int = 60, interval: float = 1.0) -> None:
        self._history_size = max(10, int(history_size))
        self._interval = max(0.25, float(interval))
        self._lock = threading.Lock()
        self._cpu_history: list[float] = []
        self._mem_history: list[float] = []
        self._sample: ResourceSample | None = None
        self._prev_net: tuple[int, int] | None = None
        self._prev_ts: float = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_started = False

    def _read_net_counters(self) -> tuple[int, int]:
        try:
            counters = psutil.net_io_counters()
            return int(counters.bytes_sent), int(counters.bytes_recv)
        except Exception:
            return 0, 0

    def _sample_once(self) -> ResourceSample:
        cpu = float(psutil.cpu_percent(interval=None))
        mem = float(psutil.virtual_memory().percent)
        sent, recv = self._read_net_counters()
        now = time.monotonic()
        delta = now - self._prev_ts
        if self._prev_net is None or delta <= 0:
            sent_kbps = recv_kbps = 0.0
        else:
            sent_kbps = max(0.0, (sent - self._prev_net[0]) / delta / 1024)
            recv_kbps = max(0.0, (recv - self._prev_net[1]) / delta / 1024)
        self._prev_net = (sent, recv)
        self._prev_ts = now
        return ResourceSample(
            cpu_percent=cpu,
            memory_percent=mem,
            net_sent_bytes=sent,
            net_recv_bytes=recv,
            net_sent_kbps=sent_kbps,
            net_recv_kbps=recv_kbps,
        )

    def _run(self) -> None:
        self._prev_ts = time.monotonic()
        while not self._stop.is_set():
            try:
                sample = self._sample_once()
            except Exception:
                time.sleep(self._interval)
                continue
            with self._lock:
                self._sample = sample
                self._cpu_history.append(sample.cpu_percent)
                self._mem_history.append(sample.memory_percent)
                if len(self._cpu_history) > self._history_size:
                    self._cpu_history.pop(0)
                if len(self._mem_history) > self._history_size:
                    self._mem_history.pop(0)
            time.sleep(self._interval)

    def start(self) -> None:
        if self._thread_started:
            return
        self._thread_started = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-resource-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def snapshot(self) -> ResourceSample:
        with self._lock:
            if self._sample is not None:
                return self._sample
        sample = self._sample_once()
        with self._lock:
            self._sample = sample
        return sample

    def history(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cpu": list(self._cpu_history),
                "memory": list(self._mem_history),
                "max": self._history_size,
            }

    def chart(self) -> dict[str, Any]:
        sample = self.snapshot()
        history = self.history()
        return {
            "cpu_percent": round(sample.cpu_percent, 1),
            "memory_percent": round(sample.memory_percent, 1),
            "net_sent_kbps": round(sample.net_sent_kbps, 1),
            "net_recv_kbps": round(sample.net_recv_kbps, 1),
            "cpu_history": history["cpu"],
            "memory_history": history["memory"],
        }
