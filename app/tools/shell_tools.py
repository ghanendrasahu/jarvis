from __future__ import annotations

import re
import subprocess
from typing import Any

from app.config import Settings
from app.tools.registry import ToolRegistry

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\brmdir\s+/s\b",
    r"\bdel\s+/f\b",
    r"\bdel\s+/q\b",
    r"\brd\s+/s\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bformat\b",
    r"\breg\s+delete\b",
    r"\breg\s+add\b",
    r"\bremove-item\b",
    r"\bclear-content\b",
    r"\bformat-volume\b",
    r"\bdiskpart\b",
    r"\bwevtutil\b",
    r"\bwmic\b",
    r"\bschtasks\b",
    r"\battrib\s+\+s",
    r"\bcertutil\s+-urlcache\b",
    r"\bnet\s+(?:user|localgroup)\b",
    r"\bvssadmin\b",
    r"\bntdsutil\b",
]

ENCODED_COMMAND_PATTERNS = [
    r"-enc(?:odedcommand)?\s+",
    r"Invoke-Expression",
    r"\biex\b",
    r"Start-Process",
    r"DownloadString",
    r"Invoke-WebRequest\s+",
    r"New-Object\s+System\.Net\.WebClient",
    r"Bypass",
    r"-ExecutionPolicy\s+Bypass",
]

COMPOUND_SEPARATORS = [";", "&&", "||", "|", "`n"]


def _rejected_by_policy(command: str) -> str | None:
    lowered = command.lower()

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, lowered):
            return "Command rejected by safety policy (dangerous pattern)."

    for pattern in ENCODED_COMMAND_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return "Command rejected by safety policy (obfuscated/remote execution pattern)."

    shell_safe = command.replace("`;", "")
    for separator in COMPOUND_SEPARATORS:
        if separator in shell_safe:
            return "Command rejected: compound/pipe operators are not allowed for shell tool."

    return None


def register_shell_tools(registry: ToolRegistry, settings: Settings) -> None:
    allowed = {item.strip().lower() for item in settings.allowed_shell_commands if item.strip()}

    def run_shell_command(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        timeout_seconds = int(args.get("timeout_seconds", 30))
        if not command:
            raise ValueError("Missing command.")

        first_token = command.split(maxsplit=1)[0].strip().lower()
        if first_token not in allowed:
            raise PermissionError(
                f"Command prefix '{first_token}' is not allowed. Allowed prefixes: {sorted(allowed)}"
            )

        rejected_reason = _rejected_by_policy(command)
        if rejected_reason:
            raise PermissionError(rejected_reason)

        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, min(timeout_seconds, 120)),
        )

        return {
            "command": command,
            "return_code": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    registry.register(
        name="run_shell_command",
        description="Run a constrained PowerShell command using approved prefixes.",
        risk="high",
        parameters={"command": "string", "timeout_seconds": "integer"},
        handler=run_shell_command,
    )
