"""The evaluator is the only thing allowed to produce a number, so it has to be strict."""

from __future__ import annotations

import pytest

from port_tariff_agent.agent.calculator import evaluate
from port_tariff_agent.errors import CalculationError


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3", 5.0),
        ("10 - 4 * 2", 2.0),
        ("(1 + 2) * 3", 9.0),
        ("7 / 2", 3.5),
        ("-5 + 1", -4.0),
        ("+5", 5.0),
        ("2 * (100 + 3 * 1.5)", 209.0),
    ],
)
def test_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("ceil(513.01)", 514.0),
        ("floor(513.99)", 513.0),
        ("round(2.345, 2)", 2.35),
        ("min(3, 9)", 3.0),
        ("max(3, 9)", 9.0),
        ("ceil(51300 / 100)", 513.0),
    ],
)
def test_the_whitelisted_functions(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


def test_a_rate_keeps_the_spaces_it_was_printed_with() -> None:
    """The transcription copies numbers as printed, so a digit-group space must not fail."""
    assert evaluate("2 * (1 234.50 + 10)") == 2489.0


@pytest.mark.parametrize("expression", ["2 2", "1 23", "1 2345"])
def test_only_a_thousands_group_is_joined(expression: str) -> None:
    """A space anywhere else is a typo, not a separator, and must not silently join digits."""
    with pytest.raises(CalculationError):
        evaluate(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "gt * 2",
        "__import__('os').system('echo')",
        "(1).__class__",
        "2 ** 64",
        "1 if 2 else 3",
        "[1, 2][0]",
        "sum([1, 2])",
        "1 < 2",
        "lambda: 1",
        "True + 1",
        "'12' + '3'",
        "",
        "   ",
        "2 +",
    ],
)
def test_everything_else_is_rejected(expression: str) -> None:
    with pytest.raises(CalculationError):
        evaluate(expression)


def test_division_by_zero_is_an_error_not_a_crash() -> None:
    with pytest.raises(CalculationError, match="division by zero"):
        evaluate("1 / 0")


def test_a_function_called_wrongly_is_an_error_not_a_crash() -> None:
    with pytest.raises(CalculationError, match="wrong arguments"):
        evaluate("ceil(1, 2, 3)")


def test_the_message_names_what_was_rejected() -> None:
    with pytest.raises(CalculationError, match="'gt'"):
        evaluate("gt / 100")
