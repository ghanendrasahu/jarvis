from types import SimpleNamespace

import pytest

import app.core.resources as resources


class FakeMonitor:
    def snapshot(self):
        return SimpleNamespace(cpu_percent=12.5, memory_percent=50.0, net_sent_kbps=10.0, net_recv_kbps=20.0)

    def history(self):
        return {"cpu": [10.0, 12.5], "memory": [50.0, 51.0]}


def test_collect_resources_shape(monkeypatch):
    monkeypatch.setattr(resources, "_monitor", FakeMonitor())
    monkeypatch.setattr(
        resources.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(used=8 * 1024**3, total=16 * 1024**3, percent=50.0),
    )
    monkeypatch.setattr(
        resources.psutil,
        "disk_usage",
        lambda _path: SimpleNamespace(used=100 * 1024**3, total=200 * 1024**3, percent=50.0),
    )
    monkeypatch.setattr(resources, "get_gpu_status", lambda: {"name": "Intel UHD", "utilization": 3.0})

    out = resources.collect_resources()
    assert out["cpu_percent"] == 12.5
    assert out["memory_percent"] == 50.0
    assert out["memory_used_gb"] == 8.0
    assert out["disk_percent"] == 50.0
    assert out["gpu"]["name"] == "Intel UHD"
    assert out["gpu"]["utilization"] == 3.0


def test_collect_resources_gpu_fallback(monkeypatch):
    monkeypatch.setattr(resources, "_monitor", FakeMonitor())
    monkeypatch.setattr(resources, "get_gpu_status", lambda: (_ for _ in ()).throw(RuntimeError("no gpu")))

    out = resources.collect_resources()
    assert out["gpu"]["name"] == "Unknown GPU"
    assert out["gpu"]["utilization"] is None


def test_gpu_status_probe_shape():
    gpu = resources.get_gpu_status()
    assert isinstance(gpu, dict)
    assert "name" in gpu and "utilization" in gpu
    assert isinstance(gpu["name"], str)

    if gpu["utilization"] is None:
        pytest.skip("GPU counters unavailable on this machine")
    assert gpu["utilization"] >= 0.0
