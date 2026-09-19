"""The arithmetic the model is not allowed to do itself (ADR-008).

An expression is parsed, not executed: only the node types below survive the walk, so a
name, an attribute, a subscript or a call to anything outside `FUNCTIONS` is rejected
before any value is produced.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Callable

from ..errors import CalculationError

type Number = int | float

FUNCTIONS: dict[str, Callable[..., Number]] = {
    "ceil": math.ceil,
    "floor": math.floor,
    "round": round,
    "min": min,
    "max": max,
}

OPERATORS: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
}

# A rate copied from the document keeps the separator it was printed with, a space
# before a group of three digits, which is a syntax error until it is removed. Narrow
# on purpose: a space anywhere else is a typo the model should see, not digits joined.
DIGIT_GROUP_SPACE = re.compile(r"(?<=\d)[ \t\u00a0\u202f\u2009](?=\d{3}(?!\d))")


def evaluate(expression: str) -> float:
    """The value of a whitelisted arithmetic expression."""
    cleaned = DIGIT_GROUP_SPACE.sub("", expression.strip())
    if not cleaned:
        raise CalculationError("the expression is empty")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalculationError(f"{expression!r} is not a valid expression: {exc.msg}") from exc
    return float(_eval(tree.body))


def _eval(node: ast.AST) -> Number:
    """Integers stay integers: `round(x, 2)` needs a whole number of digits, not 2.0."""
    match node:
        case ast.Constant(value=bool()):
            raise CalculationError("booleans are not numbers")
        case ast.Constant(value=int() | float() as value):
            return value
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_eval(operand)
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            return _eval(operand)
        case ast.BinOp(left=left, op=op, right=right) if type(op) in OPERATORS:
            return _binary(op, _eval(left), _eval(right))
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in FUNCTIONS:
            return _apply(name, [_eval(arg) for arg in args])
        case _:
            raise CalculationError(f"{_describe(node)} is not allowed in an expression")


def _binary(op: ast.operator, left: Number, right: Number) -> Number:
    try:
        return OPERATORS[type(op)](left, right)
    except ZeroDivisionError as exc:
        raise CalculationError("division by zero") from exc


def _apply(name: str, args: list[Number]) -> Number:
    try:
        return FUNCTIONS[name](*args)
    except TypeError as exc:
        raise CalculationError(f"{name} was called with the wrong arguments") from exc


def _describe(node: ast.AST) -> str:
    match node:
        case ast.Name(id=name):
            return f"the name {name!r}"
        case ast.Call(func=ast.Name(id=name)):
            return f"a call to {name!r}"
        case ast.Constant(value=value):
            return f"the literal {value!r}"
        case _:
            return f"{type(node).__name__}"
