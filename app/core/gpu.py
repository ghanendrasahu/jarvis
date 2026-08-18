from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes

_PDH = ctypes.WinDLL("pdh.dll")
_PDH.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_PDH.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_PDH.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
_PDH.PdhGetFormattedCounterArrayW.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_PDH.PdhCloseQuery.argtypes = [ctypes.c_void_p]

_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = ctypes.c_long(0x800007D2).value


class _FmtValue(ctypes.Union):
    _fields_ = [
        ("longValue", ctypes.c_long),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
    ]


class _FmtCounter(ctypes.Structure):
    _fields_ = [("c_status", wintypes.DWORD), ("value", _FmtValue)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("fmt", _FmtCounter)]


def _pdh_sum(counter_path: str) -> float | None:
    query = ctypes.c_void_p()
    counter = ctypes.c_void_p()
    if _PDH.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
        return None
    try:
        if _PDH.PdhAddEnglishCounterW(query, counter_path, 0, ctypes.byref(counter)) != 0:
            return None
        _PDH.PdhCollectQueryData(query)
        _PDH.PdhCollectQueryData(query)
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        if _PDH.PdhGetFormattedCounterArrayW(counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None) != _PDH_MORE_DATA:
            return None
        if size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if _PDH.PdhGetFormattedCounterArrayW(counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        total = 0.0
        samples = 0
        for i in range(count.value):
            item = items[i]
            if item.fmt.c_status == 0:
                total += item.fmt.value.doubleValue
                samples += 1
        return total if samples else None
    finally:
        _PDH.PdhCloseQuery(query)


_name_cache: str | None = None


def _adapter_name() -> str:
    global _name_cache
    if _name_cache is not None:
        return _name_cache
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join ' | '",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        _name_cache = (out.stdout or "Unknown GPU").strip() or "Unknown GPU"
    except Exception:
        _name_cache = "Unknown GPU"
    return _name_cache


_cache: dict[str, object] = {}


def get_gpu_status() -> dict[str, object]:
    """Live GPU utilization and video-memory usage via Windows PDH counters (in-process)."""
    global _cache
    now = time.monotonic()
    cached = _cache.get("value")
    if cached is not None and now - float(_cache.get("at", 0)) < 3.0:
        return dict(cached)  # type: ignore[arg-type]

    utilization = _pdh_sum(r"\GPU Engine(*)\Utilization Percentage")
    mem_bytes = _pdh_sum(r"\GPU Adapter Memory(*)\Dedicated Usage")
    if not mem_bytes:
        mem_bytes = _pdh_sum(r"\GPU Adapter Memory(*)\Total Committed")
    status = {
        "name": _adapter_name(),
        "utilization": round(utilization, 1) if utilization is not None else None,
        "memory_used_mb": round(mem_bytes / (1024 * 1024), 0) if mem_bytes else None,
        "memory_total_mb": None,
    }
    _cache = {"value": status, "at": now}
    return status
