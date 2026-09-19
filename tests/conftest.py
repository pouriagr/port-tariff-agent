"""Shared fixtures.

The default suite runs offline: CI has no API key, so a test that opens a socket is a bug.
`--live` opts back in for the handful of tests that call the real API.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfWriter

from port_tariff_agent.models import (
    Charge,
    ChargesFile,
    DocumentRow,
    Payer,
    SectionNode,
    TariffIndexFile,
)
from port_tariff_agent.paths import DocumentPaths
from port_tariff_agent.registry import upsert_row
from port_tariff_agent.settings import Settings
from port_tariff_agent.storage import write_json


@dataclass(frozen=True)
class Document:
    data_dir: Path
    row: DocumentRow
    port: str


FIXTURES = Path(__file__).parent / "fixtures"
ENV_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL_AGENT",
    "GEMINI_MODEL_EXTRACT",
    "GEMINI_MODEL_CLASSIFY",
    "DATA_DIR",
    "INGEST_CONCURRENCY",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live", action="store_true", default=False, help="run tests that call the real API"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def _is_live(request: pytest.FixtureRequest) -> bool:
    return request.node.get_closest_marker("live") is not None


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if _is_live(request):
        return

    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("this test must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(autouse=True)
def clean_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No test may read the developer's real configuration or write into the repo."""
    if _is_live(request):
        return
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        gemini_api_key="test-key",  # type: ignore[arg-type]
        gemini_model_agent="model-agent",
        gemini_model_extract="model-extract",
        gemini_model_classify="model-classify",
        data_dir=tmp_path / "data",
        llm_base_delay_s=0,
    )


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Callable[[int], Path]:
    """A real PDF of N pages, each a different width so order is checkable."""

    def build(pages: int, name: str = "tiny.pdf") -> Path:
        writer = PdfWriter()
        for index in range(pages):
            writer.add_blank_page(width=200 + index, height=100)
        path = tmp_path / name
        with path.open("wb") as stream:
            writer.write(stream)
        return path

    return build


@pytest.fixture
def load_markdown() -> Callable[[str], str]:
    def load(name: str) -> str:
        return (FIXTURES / "markdown" / f"{name}.md").read_text(encoding="utf-8")

    return load


DOCUMENT_HASH = "0f1e2d3c4b5a"
PORT = "Northaven"
SECTIONS = [
    (
        "1",
        "HARBOUR CHARGES",
        ["1.1", "1.2", "1.3"],
        3,
        "Charges in this part are payable by the vessel.",
    ),
    ("1.1", "GENERAL", [], 3, "Working hours are 06:00 to 18:00. Tonnage means gross tonnage."),
    (
        "1.2",
        "ARRIVAL FEE",
        [],
        4,
        "| Port | Rate per 100 tons or part thereof |\n| Northaven | 2.50 |",
    ),
    ("1.3", "MOORING FEE", [], 5, "| Port | Rate per service |\n| Other Ports | 40.00 |"),
]
CHARGES = [
    ("1.2", "Arrival Fee", "Payable on arrival at any port"),
    ("1.3", "Mooring Fee", "Payable per mooring service"),
]


@pytest.fixture
def document(tmp_path: Path) -> Document:
    """A whole ingested document on disk, small enough to reason about in a test.

    Invented port and charge names: a test that leaned on the real tariff would stop
    proving that the code is document-agnostic.
    """
    data_dir = tmp_path / "data"
    paths = DocumentPaths(data_dir, DOCUMENT_HASH)
    paths.ensure()
    write_json(
        paths.index_json,
        TariffIndexFile(
            document_hash=DOCUMENT_HASH,
            sections=[
                SectionNode(
                    id=section_id,
                    title=title,
                    parent=section_id.rsplit(".", 1)[0] if "." in section_id else None,
                    children=children,
                    order=order,
                    pdf_page=2,
                    printed_page=page,
                    text=text,
                )
                for order, (section_id, title, children, page, text) in enumerate(SECTIONS)
            ],
        ),
    )
    write_json(
        paths.charges_json,
        ChargesFile(
            document_hash=DOCUMENT_HASH,
            prompt_version=1,
            model="model-classify",
            charges=[
                Charge(
                    section_id=section_id,
                    name=name,
                    payer=Payer.VESSEL,
                    applies_when=applies_when,
                )
                for section_id, name, applies_when in CHARGES
            ],
        ),
    )
    row = DocumentRow(
        document_hash=DOCUMENT_HASH,
        source="synthetic.pdf",
        issuer="Harbour Authority",
        title="Synthetic Tariff",
        currency="XTS",
        valid_from=date(2024, 1, 1),
        valid_to=date(2024, 12, 31),
        ports=[PORT],
        page_count=2,
        ingested_at="2026-01-01T00:00:00Z",
    )
    upsert_row(data_dir, row)
    return Document(data_dir=data_dir, row=row, port=PORT)
