from __future__ import annotations

import re
import subprocess
from typing import Any

from app.config import Settings
from app.tools.registry import ToolRegistry

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bdel\s+/f\b",
    r"\bshutdown\b",
    r"\bformat\b",
    r"\breg\s+delete\b",
    r"\bremove-item\b",
]


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

        lowered = command.lower()
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, lowered):
                raise PermissionError("Command rejected by safety policy.")

        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
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

