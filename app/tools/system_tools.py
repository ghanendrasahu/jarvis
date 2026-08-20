from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from app.config import Settings
from app.core.diagnoser import DiagnosisToolHandler, SystemDiagnoser
from app.core.monitor import ResourceMonitor
from app.tools.registry import ToolRegistry

APP_ALIASES = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
    "notepad": "notepad.exe",
    "vscode": "Code.exe",
    "code": "Code.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
}

WEB_APP_ALIASES = {
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "chatgpt": "https://chatgpt.com",
}

PROTECTED_PROCESSES = {
    "system",
    "wininit.exe",
    "csrss.exe",
    "smss.exe",
    "services.exe",
    "lsass.exe",
}


def register_system_tools(registry: ToolRegistry, settings: Settings, monitor: ResourceMonitor | None = None) -> None:
    def _shell_open(target: str) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(None, "open", target, None, None, 1)
            return int(result) > 32
        except Exception:
            return False

    def _lookup_registry_app_path(exe_name: str) -> str | None:
        if os.name != "nt":
            return None
        try:
            import winreg
        except Exception:
            return None

        sub_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, sub_key) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    path = str(value).strip()
                    if path and os.path.exists(path):
                        return path
            except OSError:
                continue
        return None

    def _lookup_common_install_path(exe_name: str) -> str | None:
        basename = exe_name.lower()
        roots = [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        known_relatives: dict[str, list[str]] = {
            "chrome.exe": [
                r"Google\Chrome\Application\chrome.exe",
            ],
            "brave.exe": [
                r"BraveSoftware\Brave-Browser\Application\brave.exe",
            ],
            "msedge.exe": [
                r"Microsoft\Edge\Application\msedge.exe",
            ],
            "code.exe": [
                r"Microsoft VS Code\Code.exe",
                r"Programs\Microsoft VS Code\Code.exe",
            ],
            "firefox.exe": [
                r"Mozilla Firefox\firefox.exe",
            ],
        }
        for root in roots:
            if not root:
                continue
            for relative in known_relatives.get(basename, []):
                candidate = Path(root) / relative
                if candidate.exists():
                    return str(candidate)
        return None

    def _shortcut_roots() -> list[Path]:
        roots: list[Path] = [Path.home() / "Desktop"]
        one_drive = os.environ.get("OneDrive")
        if one_drive:
            roots.append(Path(one_drive) / "Desktop")

        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

        program_data = os.environ.get("ProgramData")
        if program_data:
            roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        return roots

    def _resolve_shortcut(name: str) -> str | None:
        candidate = name.strip().lower()
        if not candidate:
            return None
        candidate = candidate.removesuffix(".exe").removesuffix(".lnk")

        for root in _shortcut_roots():
            if not root.exists():
                continue
            exact_match: Path | None = None
            fuzzy_match: Path | None = None
            links = list(root.glob("*.lnk"))
            try:
                links.extend(root.rglob("*.lnk"))
            except Exception:
                pass
            for link in links:
                stem = link.stem.strip().lower()
                if stem == candidate:
                    exact_match = link
                    break
                if candidate in stem and fuzzy_match is None:
                    fuzzy_match = link
            if exact_match:
                return str(exact_match)
            if fuzzy_match:
                return str(fuzzy_match)
        return None

    def _resolve_executable(target: str) -> str | None:
        candidates = [target]
        if not target.lower().endswith(".exe"):
            candidates.append(f"{target}.exe")

        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
            which_hit = shutil.which(candidate)
            if which_hit:
                return which_hit
            reg_hit = _lookup_registry_app_path(candidate)
            if reg_hit:
                return reg_hit
            common_hit = _lookup_common_install_path(candidate)
            if common_hit:
                return common_hit
        return None

    def open_application(args: dict[str, Any]) -> dict[str, Any]:
        raw_name = str(args.get("name", "")).strip()
        if not raw_name:
            raise ValueError("Missing application name.")

        normalized = raw_name.lower().strip()
        if normalized in {"open", "start", "launch"}:
            raise ValueError("Application name is missing. Example: open chrome")

        target = APP_ALIASES.get(normalized, raw_name)
        if target.startswith("http://") or target.startswith("https://"):
            webbrowser.open(target, new=2)
            return {"message": "URL opened in browser.", "target": target, "kind": "url"}

        resolved = _resolve_executable(target)
        if resolved:
            subprocess.Popen([resolved], shell=False)
            return {"message": "Application launch requested.", "target": resolved, "kind": "app"}

        shortcut = _resolve_shortcut(target) or _resolve_shortcut(raw_name)
        if shortcut and _shell_open(shortcut):
            return {"message": "Desktop shortcut opened.", "target": shortcut, "kind": "app_shortcut"}

        for shell_target in (raw_name, target):
            if _shell_open(shell_target):
                return {"message": "Application launch requested (shell).", "target": shell_target, "kind": "app"}

        if normalized in WEB_APP_ALIASES:
            url = WEB_APP_ALIASES[normalized]
            webbrowser.open(url, new=2)
            return {"message": "Web app opened in browser.", "target": url, "kind": "url"}

        raise FileNotFoundError(
            f"Application '{raw_name}' not found. "
            "Use 'open url <site>' for websites or install the app."
        )

    def close_application(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name", "")).strip().lower()
        force = bool(args.get("force", False))
        if not name:
            raise ValueError("Missing application name.")

        closed: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name"]):
            proc_name = (proc.info.get("name") or "").lower()
            if not proc_name or name not in proc_name:
                continue
            if proc_name in PROTECTED_PROCESSES:
                continue
            try:
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                closed.append({"pid": proc.pid, "name": proc_name})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {"message": "Close action completed.", "closed_count": len(closed), "closed": closed}

    def list_processes(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 25))
        rows: list[dict[str, Any]] = []
        proc_list = list(psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]))
        for proc in proc_list:
            try:
                proc.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if proc_list:
            time.sleep(0.1)
        for proc in proc_list:
            try:
                rows.append(
                    {
                        "pid": proc.info.get("pid"),
                        "name": proc.info.get("name"),
                        "cpu_percent": proc.cpu_percent(None),
                        "memory_mb": round((proc.info["memory_info"].rss / 1024 / 1024), 2)
                        if proc.info.get("memory_info")
                        else None,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows = sorted(rows, key=lambda row: row.get("memory_mb") or 0, reverse=True)
        return {"count": len(rows), "processes": rows[:limit]}

    def system_status(_: dict[str, Any]) -> dict[str, Any]:
        disk = psutil.disk_usage("/")
        return {
            "platform": platform.platform(),
            "hostname": platform.node(),
            "time": datetime.now().isoformat(),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": disk.percent,
            "uptime_seconds": int(datetime.now().timestamp() - psutil.boot_time()),
        }

    def gpu_status(_: dict[str, Any]) -> dict[str, Any]:
        from app.core.gpu import get_gpu_status

        status = get_gpu_status()
        return {
            "name": status.get("name", "Unknown GPU"),
            "available": status.get("available", False),
            "utilization": status.get("utilization"),
            "memory_used_mb": status.get("memory_used_mb"),
            "memory_total_mb": status.get("memory_total_mb"),
            "unavailable_reason": status.get("unavailable_reason"),
        }

    def get_current_time(_: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        return {
            "iso": now.isoformat(),
            "time_24h": now.strftime("%H:%M:%S"),
            "time_12h": now.strftime("%I:%M:%S %p"),
            "date": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
        }

    def list_app_shortcuts(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(2000, int(args.get("limit", 200))))
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        for root in _shortcut_roots():
            if not root.exists():
                continue
            links: list[Path] = []
            links.extend(root.glob("*.lnk"))
            links.extend(root.glob("*.url"))
            try:
                links.extend(root.rglob("*.lnk"))
                links.extend(root.rglob("*.url"))
            except Exception:
                pass
            for link in links:
                key = f"{link.stem.lower()}::{str(link.resolve()).lower()}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "name": link.stem,
                        "path": str(link.resolve()),
                        "extension": link.suffix.lower(),
                        "source_root": str(root.resolve()),
                    }
                )

        items = sorted(items, key=lambda item: item["name"].lower())
        return {"count": len(items), "items": items[:limit]}

    def list_known_apps(_: dict[str, Any]) -> dict[str, Any]:
        desktop_apps: list[dict[str, Any]] = []
        for alias, target in sorted(APP_ALIASES.items()):
            resolved = _resolve_executable(target)
            desktop_apps.append(
                {
                    "alias": alias,
                    "target": target,
                    "resolved_path": resolved,
                    "available": bool(resolved),
                }
            )

        web_apps = [{"alias": alias, "url": url} for alias, url in sorted(WEB_APP_ALIASES.items())]
        return {
            "desktop_apps": desktop_apps,
            "web_apps": web_apps,
            "shortcut_roots": [str(root) for root in _shortcut_roots() if root.exists()],
        }

    registry.register(
        name="open_application",
        description="Open a desktop application by name or path.",
        risk="low",
        parameters={"name": "string"},
        handler=open_application,
    )
    registry.register(
        name="close_application",
        description="Close process(es) containing the given application name.",
        risk="medium",
        parameters={"name": "string", "force": "boolean"},
        handler=close_application,
    )
    registry.register(
        name="list_processes",
        description="List currently running processes.",
        risk="low",
        parameters={"limit": "integer"},
        handler=list_processes,
    )
    registry.register(
        name="system_status",
        description="Return CPU, memory, disk, uptime, and host details.",
        risk="low",
        parameters={},
        handler=system_status,
    )
    registry.register(
        name="get_current_time",
        description="Return local date and time.",
        risk="low",
        parameters={},
        handler=get_current_time,
    )
    registry.register(
        name="list_app_shortcuts",
        description="List desktop/start-menu shortcuts that can be opened by name.",
        risk="low",
        parameters={"limit": "integer"},
        handler=list_app_shortcuts,
    )
    registry.register(
        name="list_known_apps",
        description="List built-in app aliases and known web-app aliases.",
        risk="low",
        parameters={},
        handler=list_known_apps,
    )

    # Diagnosis tool registration. Wired to the shared ResourceMonitor when one is
    # available (bootstrap always provides it); falls back to an informational stub
    # otherwise so the registry stays consistent in minimal/test setups.
    if monitor is not None:
        diagnose_handler = DiagnosisToolHandler(SystemDiagnoser(monitor, settings)).handle
    else:

        def diagnose_handler(_: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "info",
                "explanation": "System diagnosis requires the resource monitor to be active. Use 'system_status' for current metrics.",
                "diagnosis": None,
                "raw_metrics": None,
            }

    registry.register(
        name="diagnose_system",
        description="Diagnose system issues (high CPU, RAM, disk, GPU) with reasoning and suggested actions. Never auto-kills.",
        risk="low",
        parameters={},
        handler=diagnose_handler,
    )
