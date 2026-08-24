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
    # Additional arbitrary-code-execution primitives (S1 hardening).
    r"\bnew-object\b",
    r"\badd-type\b",
    r"\binvoke-command\b",
    r"\binvoke-item\b",
    r"\bregister-scheduledtask\b",
]

# Compound operators, including raw newlines: a newline would otherwise let a
# second, unchecked command execute behind an approved prefix (PowerShell runs
# each line of -Command input separately).
COMPOUND_SEPARATORS = [";", "&&", "||", "|", "`n", "\n", "\r", "\0"]

# PowerShell expression/eval primitives: subexpressions, escape sequences,
# static member invocation, and script-block literals all enable arbitrary
# code execution behind an allowed first token.
BLOCKED_METACHARACTERS = ["$(", "`", "::", "{", "}"]

# Interpreters and script hosts are refused even when a user explicitly adds
# them to allowed_shell_commands: a single allowlisted interpreter turns the
# prefix policy into unrestricted code execution.
INTERPRETER_TOKENS = {
    "python", "python3", "py", "pip", "pip3",
    "powershell", "pwsh", "cmd",
    "node", "npm", "npx", "perl", "ruby", "php", "lua",
    "bash", "sh", "zsh", "wsl",
    "mshta", "rundll32", "regsvr32", "msbuild", "installutil", "bitsadmin",
    "cscript", "wscript",
}

# git argument forms that execute attacker-controlled commands.
GIT_UNSAFE_OPTIONS_EXACT = {"-c", "-u", "--upload-pack", "--receive-pack", "--exec"}
GIT_UNSAFE_OPTIONS_PREFIX = ("--upload-pack=", "--receive-pack=", "--exec=")
# git subcommands whose primary purpose involves running external code
# (config hooks/filters/FS monitors, credential helpers, bisect run,
# filter-branch tree/index filters, hook scripts, submodule updates).
GIT_UNSAFE_SUBCOMMANDS = {
    "config", "credential", "daemon", "instaweb", "shell", "bisect",
    "filter-branch", "hook", "submodule", "p4", "upload-archive",
}
# Global options that consume the following token as their value.
GIT_VALUE_GLOBALS = {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--exec-path"}


def _rejected_by_policy(command: str) -> str | None:
    lowered = command.lower()

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, lowered):
            return "Command rejected by safety policy (dangerous pattern)."

    for pattern in ENCODED_COMMAND_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return "Command rejected by safety policy (obfuscated/remote execution pattern)."

    for metacharacter in BLOCKED_METACHARACTERS:
        if metacharacter in command:
            return "Command rejected: shell expression/metacharacter syntax is not allowed."

    for separator in COMPOUND_SEPARATORS:
        if separator in command:
            return "Command rejected: compound/pipe operators are not allowed for shell tool."

    return None


def _git_violation(command: str) -> str | None:
    """Reject git argument forms that escalate into arbitrary code execution."""
    tokens = command.split()
    lowered_tokens = [token.lower() for token in tokens]

    if any(token.startswith("ext::") for token in lowered_tokens):
        return "Command rejected: git 'ext::' transports can execute arbitrary commands."

    for token in lowered_tokens[1:]:
        if token in GIT_UNSAFE_OPTIONS_EXACT or any(token.startswith(prefix) for prefix in GIT_UNSAFE_OPTIONS_PREFIX):
            return f"Command rejected: git option '{token}' can execute external commands."

    index = 1
    while index < len(lowered_tokens) and lowered_tokens[index].startswith("-"):
        if tokens[index] in GIT_VALUE_GLOBALS:
            index += 1  # skip the option's value
        index += 1
    subcommand = lowered_tokens[index] if index < len(lowered_tokens) else ""
    if subcommand in GIT_UNSAFE_SUBCOMMANDS:
        return f"Command rejected: git '{subcommand}' can execute external code."

    return None


def register_shell_tools(registry: ToolRegistry, settings: Settings) -> None:
    allowed = {item.strip().lower() for item in settings.allowed_shell_commands if item.strip()}

    def run_shell_command(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        timeout_seconds = int(args.get("timeout_seconds", 30))
        if not command:
            raise ValueError("Missing command.")

        first_token = command.split(maxsplit=1)[0].strip().lower()
        if first_token in INTERPRETER_TOKENS:
            raise PermissionError(
                f"Command prefix '{first_token}' can execute arbitrary code and is always disallowed."
            )
        if first_token not in allowed:
            raise PermissionError(
                f"Command prefix '{first_token}' is not allowed. Allowed prefixes: {sorted(allowed)}"
            )

        if first_token == "git":
            git_reason = _git_violation(command)
            if git_reason:
                raise PermissionError(git_reason)

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
