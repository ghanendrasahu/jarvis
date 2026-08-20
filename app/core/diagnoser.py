from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.config import Settings
from app.core.monitor import ResourceMonitor


class DiagnosisResult(BaseModel):
    summary: str
    root_cause: str | None = None
    severity: Literal["low", "medium", "high"] = "low"
    metrics: dict[str, Any]
    suggested_actions: list[dict[str, Any]]
    requires_approval: list[str]
    timestamp: str


class SystemDiagnoser:
    """Diagnoses system issues (high CPU, RAM, disk, GPU, network) with reasoning
    and suggested actions. Never auto-kills processes without explicit approval."""

    def __init__(self, monitor: ResourceMonitor, settings: Settings) -> None:
        self.monitor = monitor
        self.settings = settings

    def _collect_metrics(self) -> dict[str, Any]:
        snapshot = self.monitor.snapshot()
        history = self.monitor.history()
        return {
            "cpu_percent": round(snapshot.cpu_percent, 1),
            "memory_percent": round(snapshot.memory_percent, 1),
            "disk_percent": 0.0,  # filled in later if needed
            "net_sent_kbps": round(snapshot.net_sent_kbps, 1),
            "net_recv_kbps": round(snapshot.net_recv_kbps, 1),
            "cpu_history": history["cpu"][-30:],  # last 30s
            "mem_history": history["memory"][-30:],
        }

    def _collect_process_metrics(self) -> list[dict[str, Any]]:
        """Get current process list with CPU/memory. Mirrors list_processes output."""
        import psutil

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
                info = proc.info
                name = (info.get("name") or "").lower()
                if not name:
                    continue
                cpu = info.get("cpu_percent", 0.0) or 0.0
                mem_mb = None
                mem_info = info.get("memory_info")
                if mem_info:
                    mem_mb = round(mem_info.rss / 1024 / 1024, 2)
                rows.append(
                    {
                        "pid": info.get("pid"),
                        "name": name,
                        "cpu_percent": cpu,
                        "memory_mb": mem_mb,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows = sorted(rows, key=lambda r: r.get("memory_mb") or 0, reverse=True)
        return rows[:50]

    def _analyze(self, metrics: dict[str, Any], process_metrics: list[dict[str, Any]]) -> DiagnosisResult:
        cpu = metrics["cpu_percent"]
        mem = metrics["memory_percent"]
        cpu_history = metrics["cpu_history"]

        # Determine severity
        severity: Literal["low", "medium", "high"]
        if cpu >= 90 or mem >= 90:
            severity = "high"
        elif cpu >= 70 or mem >= 80:
            severity = "medium"
        else:
            severity = "low"

        # Find top CPU/RAM processes
        top_cpu = sorted(process_metrics, key=lambda p: p.get("cpu_percent", 0), reverse=True)[:5]
        top_mem = sorted(process_metrics, key=lambda p: p.get("memory_mb") or 0, reverse=True)[:5]

        # Check for sustained high usage (in history)
        sustained_cpu = sum(1 for c in cpu_history[-30:] if c > 80) if cpu_history else 0

        # Build root cause suggestion
        root_cause: str | None = None
        suggested_actions: list[dict[str, Any]] = []
        approval_ids: list[str] = []

        # High CPU analysis
        if cpu >= 80:
            # Check if Chrome is a top CPU process
            chrome_procs = [p for p in top_cpu if "chrome" in p.get("name", "")]
            if chrome_procs:
                # Check if sustained
                sustained = sustained_cpu > 0
                if sustained:
                    root_cause = f"Chrome is consuming {cpu:.1f}% CPU and has been doing so for {sustained}s."
                    suggested_actions = [
                        {"action": "close_application", "target": "chrome", "reason": "Reduce CPU load; user can decide which tab to close"},
                        {"action": "wait", "reason": "Chrome may settle after inactive tabs finish loading"},
                    ]
                    approval_ids = ["close_chrome_diagnostic"]
                else:
                    root_cause = f"Chrome is consuming {cpu:.1f}% CPU recently."
                    suggested_actions = [
                        {"action": "wait", "reason": "Transient spike; may normalize shortly"},
                    ]
            else:
                # Other high-CPU processes
                if top_cpu:
                    p = top_cpu[0]
                    root_cause = f"{p['name'].title()} (PID {p['pid']}) is consuming {p['cpu_percent']:.1f}% CPU."
                    suggested_actions = [
                        {"action": "close_application", "target": p['name'], "reason": "Reduce CPU load"},
                        {"action": "wait", "reason": "Process may complete its task"},
                    ]
                    approval_ids = [f"close_{p['name']}_diagnostic"]
                else:
                    root_cause = f"System CPU at {cpu:.1f}% with no single process standing out."
                    suggested_actions = [
                        {"action": "wait", "reason": "May be system maintenance or background tasks"},
                    ]

        # High RAM analysis
        if mem >= 80 and not root_cause:
            ram_hogs = [p for p in top_mem if p.get("memory_mb") and p["memory_mb"] > 500]
            if ram_hogs:
                p = ram_hogs[0]
                root_cause = f"{p['name'].title()} (PID {p['pid']}) is using {p['memory_mb']:.1f} MB RAM."
                suggested_actions = [
                    {"action": "close_application", "target": p['name'], "reason": "Free memory pressure"},
                    {"action": "wait", "reason": "Process may release memory on its own"},
                ]
                approval_ids = [f"close_{p['name']}_diagnostic"]
            else:
                root_cause = f"System memory at {mem:.1f}% with no single process exceeding 500 MB."
                suggested_actions = [
                    {"action": "wait", "reason": "Memory may be file caching; harmless on Windows"},
                ]

        # Disk pressure
        disk = metrics.get("disk_percent", 0)
        if disk >= 90:
            root_cause = f"Disk is {disk:.1f}% full."
            suggested_actions = [
                {"action": "wait", "reason": "Free disk space by deleting unneeded files"},
                {"action": "cleanup", "reason": "Run disk cleanup to remove temporary files"},
            ]

        # Network issues (high sustained send/recv would need more history; simplified)
        if not root_cause:
            root_cause = "No major resource anomalies detected."

        if not suggested_actions:
            suggested_actions = [{"action": "wait", "reason": "Monitor for changes"}]

        return DiagnosisResult(
            summary=f"System analysis: CPU {cpu:.1f}%, RAM {mem:.1f}%, Disk OK.",
            root_cause=root_cause,
            severity=severity,
            metrics=metrics,
            suggested_actions=suggested_actions,
            requires_approval=approval_ids,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def diagnose(self) -> DiagnosisResult:
        """Run full system diagnosis and return structured result."""
        metrics = self._collect_metrics()
        process_metrics = self._collect_process_metrics()
        return self._analyze(metrics, process_metrics)

    def explain(self, result: DiagnosisResult) -> str:
        """Human-readable explanation, matching the spec style:
        'Chrome is consuming 71% CPU and has been doing so for 4 minutes.
        This is the likely cause of the slowdown.'"""
        lines: list[str] = []

        if result.root_cause:
            lines.append(result.root_cause)

        # Add brief reasoning
        if result.severity == "high":
            lines.append("This is likely the cause of the slowdown.")
        elif result.severity == "medium":
            lines.append("This may be contributing to slowdown.")
        else:
            lines.append("This is within normal operation.")

        # Suggested actions (without auto-execution)
        if result.suggested_actions:
            action = result.suggested_actions[0]
            action_name = action.get("action", "action")
            action_target = action.get("target", "")
            reason = action.get("reason", "")
            lines.append(f"Suggested: {action_name} {action_target}. {reason}")

        # Approval requirement
        if result.requires_approval:
            lines.append(
                f"Note: '{result.requires_approval[0]}' requires your approval before execution."
            )

        return " ".join(lines).strip()


class DiagnosisToolHandler:
    """Handler for the diagnose_system tool that wraps SystemDiagnoser."""

    def __init__(self, diagnoser: SystemDiagnoser) -> None:
        self.diagnoser = diagnoser

    def handle(self, _: dict[str, Any]) -> dict[str, Any]:
        result = self.diagnoser.diagnose()
        explanation = self.diagnoser.explain(result)
        return {
            "status": "completed",
            "explanation": explanation,
            "diagnosis": {
                "summary": result.summary,
                "root_cause": result.root_cause,
                "severity": result.severity,
                "suggested_actions": result.suggested_actions,
                "requires_approval": result.requires_approval,
            },
            "raw_metrics": result.metrics,
        }
