from datetime import datetime

import pytest

from app.core.utils import build_startup_greeting


@pytest.mark.parametrize(
    ("hour", "expected_part"),
    [
        (5, "morning"),
        (6, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (17, "afternoon"),
        (18, "evening"),
        (23, "evening"),
        (0, "evening"),
        (4, "evening"),
    ],
)
def test_greeting_by_time_of_day(hour: int, expected_part: str) -> None:
    greeting = build_startup_greeting(datetime(2026, 8, 24, hour, 0, 0))
    assert greeting == f"Good {expected_part}, sir. All systems are online."


def test_greeting_defaults_to_now() -> None:
    greeting = build_startup_greeting()
    assert greeting.startswith(("Good morning", "Good afternoon", "Good evening"))
    assert greeting.endswith("All systems are online.")
