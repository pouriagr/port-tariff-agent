"""Smoke test: the package and its sub-packages import.

Kept so that the test suite is never empty; real tests arrive with each phase
(see docs/roadmap.md).
"""

import importlib

import pytest

SUBPACKAGES = ["ingestion", "agent", "api", "cli"]


def test_package_imports() -> None:
    assert importlib.import_module("port_tariff_agent")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    assert importlib.import_module(f"port_tariff_agent.{name}")
