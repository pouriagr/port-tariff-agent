"""Nothing about one particular tariff may leak into the code or the prompts.

The banned words live here, in the tests, never in what ships.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "port_tariff_agent"

PORT_NAMES = [
    "durban",
    "cape town",
    "richards bay",
    "east london",
    "port elizabeth",
    "ngqura",
    "mossel bay",
    "saldanha",
]
CHARGE_NAMES = [
    "light dues",
    "port dues",
    "towage",
    "pilotage",
    "berth dues",
    "running lines",
    "vts",
    "tonnage dues",
]
ISSUER_WORDS = ["transnet", "tnpa", "sudestada"]
# A tariff amount: a thousands-separated figure, or three digits and two decimals.
RATE = re.compile(r"\b\d{1,3}(?: \d{3})+\.\d{2}\b|\b\d{3,}\.\d{2}\b")
# A model id, as opposed to a field name such as `gemini_model_extract`.
MODEL_ID = re.compile(r"gemini[-.][\w.-]*", re.IGNORECASE)


def python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def prompt_files() -> list[Path]:
    return sorted((SRC / "prompts").glob("*.md"))


def shipped_files() -> list[Path]:
    return python_files() + prompt_files()


@pytest.mark.parametrize("path", shipped_files(), ids=lambda path: path.name)
def test_no_port_or_charge_names_are_hard_coded(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    found = [word for word in PORT_NAMES + CHARGE_NAMES + ISSUER_WORDS if word in text]
    assert found == [], f"{path.name} mentions {found}"


@pytest.mark.parametrize("path", shipped_files(), ids=lambda path: path.name)
def test_no_tariff_rates_are_hard_coded(path: Path) -> None:
    assert RATE.findall(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", shipped_files(), ids=lambda path: path.name)
def test_no_model_ids_are_hard_coded(path: Path) -> None:
    assert MODEL_ID.findall(path.read_text(encoding="utf-8")) == []


def test_the_query_phase_never_imports_the_ingestion_package() -> None:
    for path in (SRC / "agent").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ingestion" not in text, f"{path.name} reaches into ingestion"


def test_only_one_module_talks_to_the_provider_sdk() -> None:
    importers = [
        path.relative_to(SRC).as_posix()
        for path in python_files()
        if re.search(r"^\s*from google|^\s*import google", path.read_text(encoding="utf-8"), re.M)
    ]
    assert importers == ["llm/client.py", "llm/retry.py"]
