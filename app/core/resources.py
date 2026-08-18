from __future__ import annotations

from typing import Any

import psutil

from app.core.gpu import get_gpu_status
from app.core.monitor import ResourceMonitor

_monitor: ResourceMonitor | None = None


def _ensure_monitor() -> ResourceMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ResourceMonitor(interval=1.0, history_size=60)
        _monitor.start()
    return _monitor


def collect_resources() -> dict[str, Any]:
    """Combined CPU / RAM / disk / GPU / network snapshot for the web dashboard."""
    monitor = _ensure_monitor()
    sample = monitor.snapshot()
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        gpu = get_gpu_status()
    except Exception:
        gpu = {"name": "Unknown GPU", "utilization": None, "memory_used_mb": None, "memory_total_mb": None}
    return {
        "cpu_percent": round(sample.cpu_percent, 1),
        "cpu_history": monitor.history()["cpu"],
        "memory_percent": round(sample.memory_percent, 1),
        "memory_used_gb": round(vm.used / (1024**3), 2),
        "memory_total_gb": round(vm.total / (1024**3), 2),
        "memory_history": monitor.history()["memory"],
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "net_sent_kbps": round(sample.net_sent_kbps, 1),
        "net_recv_kbps": round(sample.net_recv_kbps, 1),
        "gpu": gpu,
    }
