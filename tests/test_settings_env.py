"""`.env.example` is the configuration contract, so it has to list the whole of it.

A setting that exists in code and nowhere in the example file is invisible: nobody tunes a
knob they cannot see, and the first they hear of it is a traceback. The rule is in
`CLAUDE.md`; this makes it a gate rather than a habit.
"""

from __future__ import annotations

import re
from pathlib import Path

from port_tariff_agent.settings import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def documented_names() -> set[str]:
    """Every `NAME=` on a line of its own, comments excluded."""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return {match.group(1) for line in lines if (match := re.match(r"^([A-Z0-9_]+)=", line))}


def test_every_setting_is_documented() -> None:
    expected = {name.upper() for name in Settings.model_fields}

    assert expected - documented_names() == set()


def test_nothing_is_documented_that_is_not_a_setting() -> None:
    """A variable removed from the code must not linger in the example file."""
    expected = {name.upper() for name in Settings.model_fields}

    assert documented_names() - expected == set()
