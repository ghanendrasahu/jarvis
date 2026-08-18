from typing import cast

import pytest

from app.config import Settings
from app.tools.browser_action import BrowserActionTool, _normalize_steps
from app.tools.browser_session import BrowserSession


def test_normalize_accepts_valid_steps():
    steps = [
        {"action": "goto", "url": "https://example.com"},
        {"action": "fill", "selector": "input[name=email]", "value": "a@b.com"},
        {"action": "screenshot", "path": "data/x.png"},
    ]
    result = _normalize_steps(steps)
    assert len(result) == 3
    assert result[0]["action"] == "goto"


def test_normalize_rejects_empty_steps():
    with pytest.raises(ValueError):
        _normalize_steps([])
    with pytest.raises(ValueError):
        _normalize_steps(None)


def test_normalize_rejects_unknown_action():
    with pytest.raises(ValueError, match="Unsupported browser action"):
        _normalize_steps([{"action": "delete_everything"}])


def test_normalize_rejects_step_without_action():
    with pytest.raises(ValueError, match="requires an 'action'"):
        _normalize_steps([{"url": "https://example.com"}])


def test_browser_action_requires_steps_argument():
    tool = BrowserActionTool(cast(Settings, None), cast(BrowserSession, object()))
    with pytest.raises(ValueError, match="non-empty 'steps'"):
        tool.run({})
