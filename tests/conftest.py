"""Shared fixtures.

The default suite runs offline: CI has no API key, so a test that opens a socket is a bug.
`--live` opts back in for the handful of tests that call the real API.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

import pytest
from pypdf import PdfWriter

from port_tariff_agent.settings import Settings

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
