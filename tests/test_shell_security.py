"""S1 regression tests: the shell allowlist must never enable arbitrary code execution."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.config import Settings
from app.tools.registry import ToolRegistry
from app.tools.shell_tools import register_shell_tools

CANONICAL_ALLOWED = [
    "get-date",
    "get-process",
    "get-service",
    "get-childitem",
    "tasklist",
    "ipconfig",
    "ping",
    "where",
    "dir",
    "git",
]


def make_handler(allowed: list[str] | None = None) -> Any:
    settings = Settings(
        llm_provider="groq",
        allowed_shell_commands=allowed if allowed is not None else CANONICAL_ALLOWED,
    )
    registry = ToolRegistry()
    register_shell_tools(registry, settings)
    spec = registry.get("run_shell_command")
    assert spec is not None
    return spec.handler


@pytest.fixture()
def forbid_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any policy bypass must fail loudly instead of silently executing."""

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"subprocess.run must never be reached, was called with {args}")

    monkeypatch.setattr("app.tools.shell_tools.subprocess.run", _explode)


# --------------------------------------------------------------------------- interpreters


@pytest.mark.parametrize(
    "command",
    [
        'python -c "import os; os.system(\'calc.exe\')"',
        "python -m http.server 8080",
        "PY -C import os",
        "python3 script.py",
        "py -3 -c \"print('x')\"",
        "pip install evil-package",
        "PIP download evil-package",
        "pip3 install --user pwn",
        "node -e \"require('child_process').exec('calc')\"",
        "npm run preinstall",
        "powershell -c calc",
        "pwsh -Command calc",
        "cmd /c calc",
        "bash -c id",
        "wsl python3 -c x",
        "cscript //b evil.js",
        "mshta evil.hta",
        "rundll32 javascript:\"\\..\\mshtml,RunHTMLApplication\"",
        "msbuild project.proj",
    ],
)
def test_interpreter_and_script_host_tokens_always_rejected(
    forbid_execution: None, command: str
) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


def test_interpreters_rejected_even_when_user_allowlists_them(forbid_execution: None) -> None:
    """Hard deny: adding python/pip to allowed_shell_commands cannot re-enable them."""
    handler = make_handler(allowed=["python", "pip", "cmd"])
    for command in ("python -c x", "pip install x", "cmd /c calc"):
        with pytest.raises(PermissionError):
            handler({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "python.exe -c x",  # extension variant must not match the (removed) name entry
        "Python.EXE -c x",
        '"python" -c x',  # quoting the executable changes the token
        "'python' -c x",
        "& python -c x",  # PowerShell call operator is itself a disallowed prefix
        "C:\\Python313\\python.exe -c x",  # absolute paths are never allowlist matches
        "$env:comspec /c calc",  # environment-variable indirection
        "%COMSPEC% /c calc",
    ],
)
def test_interpreter_aliases_and_variants_cannot_bypass(
    forbid_execution: None, command: str
) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


def test_pip_cannot_execute_arbitrary_code(forbid_execution: None) -> None:
    handler = make_handler()
    for command in (
        "pip install ./malicious-package",  # setup.py execution on build
        "pip install -e ./local-pkg",
        "pip install pkg --config-settings x=y",
    ):
        with pytest.raises(PermissionError):
            handler({"command": command})


# --------------------------------------------------------------------------- git RCE forms


@pytest.mark.parametrize(
    "command",
    [
        "git clone ext::sh -c calc",  # ext:: transport executes commands directly
        "git fetch ext::powershell -c x",
        "git -c core.fsmonitor=/tmp/evil status",  # -c config injection
        "git -c filter.driver.clean=evil checkout main",
        "git config core.fsmonitor evil",  # config writes enable later execution
        "git credential fill",  # credential helpers execute external programs
        "git bisect run powershell -c x",  # bisect run executes arbitrary commands
        "git filter-branch --tree-filter evil main",
        "git hook run pre-commit",
        "git submodule update --init",  # submodule update can run configured commands
        "git clone --upload-pack evil repo",
        "git clone --receive-pack=evil repo",
        "git rebase --exec make test",
        "git daemon --export-all",
        "git instaweb",
        "git upload-archive repo",
        "git p4 clone //depot",
        "GIT CONFIG user.name attacker",  # case-insensitive matching
    ],
)
def test_git_argument_forms_that_execute_code_are_rejected(
    forbid_execution: None, command: str
) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


def test_git_value_taking_global_does_not_hide_subcommand(forbid_execution: None) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": "git -C repo config user.name x"})


# --------------------------------------------------------------------------- eval primitives / chaining


@pytest.mark.parametrize(
    "command",
    [
        'get-date $("calc")',  # PowerShell subexpression evaluation
        "git commit -m \"$(calc)\"",  # subexpression inside quotes
        "get-date [datetime]::now",  # static member invocation
        "get-childitem {iex}",  # script-block literals
        "get-date `(n calc",  # backtick escapes
        "get-date `n whoami",  # PowerShell newline escape
        "get-date; whoami",
        "get-date && whoami",
        "get-date || whoami",
        "get-date | measure",
        "get-date\npython -c x",  # raw newline chains an unchecked second command
        "get-date\r\nipconfig /all",
    ],
)
def test_shell_metacharacters_and_chaining_rejected(
    forbid_execution: None, command: str
) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


def test_existing_dangerous_patterns_still_rejected(forbid_execution: None) -> None:
    handler = make_handler()
    for command in ("get-childitem -Recurse; remove-item x", "invoke-expression calc", "iex calc"):
        with pytest.raises(PermissionError):
            handler({"command": command})


# --------------------------------------------------------------------------- legitimate usage preserved


@pytest.mark.parametrize(
    "command",
    [
        "get-date",
        "get-process",
        "get-service wuauserv",
        "get-childitem",
        "tasklist",
        "ipconfig /all",
        "ping -n 1 localhost",
        "where git",
        "dir",
        "git status",
        "git log -5",
        "git diff HEAD~1",
        "git add file.txt",
        "git commit -m message",
    ],
)
def test_approved_commands_still_execute(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.tools.shell_tools.subprocess.run", fake_run)
    handler = make_handler()
    result = handler({"command": command})

    assert captured["argv"] == ["powershell", "-NoProfile", "-Command", command]
    assert captured["kwargs"]["capture_output"] is True
    assert result["return_code"] == 0
    assert result["stdout"] == "ok"
    assert result["command"] == command


def test_disallowed_prefix_still_rejected(forbid_execution: None) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError, match="not allowed"):
        handler({"command": "whoami"})


def test_tool_registration_contract_unchanged() -> None:
    registry = ToolRegistry()
    register_shell_tools(registry, Settings(llm_provider="groq"))
    spec = registry.get("run_shell_command")
    assert spec is not None
    assert spec.risk == "high"
    assert spec.parameters == {"command": "string", "timeout_seconds": "integer"}


# --------------------------------------------------------------------------- S2: parsing/evaluation hardening


@pytest.mark.parametrize(
    "command",
    [
        "get-date\r\nipconfig",  # CRLF statement split
        "get-date\rwhoami",  # bare CR
        "get-date\x00whoami",  # NUL byte
        "tasklist /svc && tasklist /v",
    ],
)
def test_chaining_variants_rejected(forbid_execution: None, command: str) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "get-childitem (whoami)",  # argument-position expression runs another command
        "dir @(calc)",  # array subexpression executes its contents
        "get-date ([datetime]::new())",
        "$x = whoami",  # variable assignment/indirection
        "get-date ${env:username}",
        "dir $env:programfiles",
        "git commit -m \"(wip)\"",  # fail-closed: even quoted parentheses are refused
        "write-output & whoami",  # call-operator residue
        "get-childitem `-path x",  # escape sequences splice tokens
        "foreach-object -process { iex }",  # script-block literals
    ],
)
def test_powershell_evaluation_forms_rejected(forbid_execution: None, command: str) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        ".\\tools\\list.exe arg",  # relative executable invocation
        '"C:\\tools\\x.exe" -a',  # quoted absolute executable
        "C:\\Windows\\System32\\cmd.exe /c calc",
        "%COMSPEC% /c calc",
        "pwsh.exe -Command calc",
        "powershell_ise.exe",
        "wscript.exe evil.vbs",
        "mshta.exe evil.hta",
    ],
)
def test_executable_indirection_paths_and_variants_rejected(forbid_execution: None, command: str) -> None:
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "get-date > out.txt",
        "get-date >> out.txt",
        "get-date 2> err.txt",
        "git status 2>> err.txt",
        "get-date *> all.txt",
        "get-date 2>&1",
        "ping -n 1 localhost >| out.txt",
        "sort < in.txt",
    ],
)
def test_all_redirection_forms_rejected(forbid_execution: None, command: str) -> None:
    """No redirection is permitted: it mutates files / merges streams outside
    the read-only intent of the allowlist (S2 product decision)."""
    handler = make_handler()
    with pytest.raises(PermissionError):
        handler({"command": command})


def test_extension_disguise_cannot_bypass_interpreter_hard_deny() -> None:
    """A user-configured allowlist must not resurrect interpreters via .exe/.cmd names."""
    handler = make_handler(allowed=["python.exe", "pwsh.exe", "cmd.exe"])
    for command in ("python.exe -c x", "pwsh.exe -Command calc", "cmd.exe /c calc"):
        with pytest.raises(PermissionError):
            handler({"command": command})


def test_string_config_value_cannot_reenable_interpreters(forbid_execution: None) -> None:
    """ALLOWED_SHELL_COMMANDS parsed from a CSV env string gets the same hard-deny."""
    settings = Settings(llm_provider="groq", allowed_shell_commands="python,pip,cmd")  # type: ignore[arg-type]
    registry = ToolRegistry()
    register_shell_tools(registry, settings)
    handler = registry.get("run_shell_command")
    assert handler is not None
    with pytest.raises(PermissionError):
        handler.handler({"command": "python -c x"})  # type: ignore[union-attr]


def test_extension_stem_aliasing_of_allowlisted_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """tasklist.exe resolves to allowlisted 'tasklist': same binary, benign aliasing."""
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.tools.shell_tools.subprocess.run", fake_run)
    handler = make_handler(allowed=["tasklist"])
    result = handler({"command": "tasklist.exe"})

    assert captured["argv"][0:3] == ["powershell", "-NoProfile", "-Command"]
    assert captured["argv"][3] == "tasklist.exe"
    assert result["return_code"] == 0


@pytest.mark.parametrize(
    "command",
    [
        "git switch main",
        "git branch",
        "git remote -v",
        "git stash list",
        "ipconfig /all",
        "ping -n 1 localhost",
        "where /r . *.py",
    ],
)
def test_additional_legitimate_forms_execute(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.tools.shell_tools.subprocess.run", fake_run)
    handler = make_handler()
    result = handler({"command": command})

    assert captured["argv"] == ["powershell", "-NoProfile", "-Command", command]
    assert result["stdout"] == "ok"


def test_git_argument_scanning_covers_every_token(forbid_execution: None) -> None:
    """Unsafe git options are denied no matter where they appear after 'git'."""
    handler = make_handler()
    for command in (
        "git log --exec=/bin/sh",
        "git clone --upload-pack evil repo",
        "git fetch --receive-pack=evil origin",
        "git -u x status",
    ):
        with pytest.raises(PermissionError):
            handler({"command": command})
