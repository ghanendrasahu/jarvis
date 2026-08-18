from app.tools.shell_tools import _rejected_by_policy


def test_safe_command_allowed():
    assert _rejected_by_policy("Get-Date") is None
    assert _rejected_by_policy("git status") is None


def test_dangerous_patterns_rejected():
    assert _rejected_by_policy("Remove-Item C:/ -Recurse") is not None
    assert _rejected_by_policy("rm -rf /") is not None
    assert _rejected_by_policy("shutdown /s") is not None
    assert _rejected_by_policy("format C:") is not None


def test_obfuscated_execution_rejected():
    assert _rejected_by_policy("powershell -EncodedCommand Zm9v") is not None
    assert _rejected_by_policy("Invoke-Expression 'Get-Date'") is not None
    assert _rejected_by_policy("Start-Process calc.exe") is not None


def test_compound_commands_rejected():
    assert _rejected_by_policy("Get-Date; Remove-Item x") is not None
    assert _rejected_by_policy("Get-Date && dir") is not None
    assert _rejected_by_policy("Get-Date | Out-File out.txt") is not None
