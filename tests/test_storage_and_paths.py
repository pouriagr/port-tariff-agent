"""Path algebra, atomic writes and hashing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from port_tariff_agent.errors import ConfigError
from port_tariff_agent.paths import DocumentPaths
from port_tariff_agent.settings import Settings, load_settings
from port_tariff_agent.storage import (
    append_jsonl,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_text,
    write_json,
    write_text,
)


def test_sha256_matches_hashlib() -> None:
    assert sha256_bytes(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()


def test_paths_derive_from_data_dir_and_hash(tmp_path: Path) -> None:
    paths = DocumentPaths(data_dir=tmp_path, document_hash="deadbeef")
    assert paths.dir == tmp_path / "deadbeef"
    assert paths.tariff_md == tmp_path / "deadbeef" / "tariff.md"
    assert DocumentPaths.registry_file(tmp_path) == tmp_path / "documents.json"


@pytest.mark.parametrize(
    ("page", "name"), [(1, "page_001.md"), (7, "page_007.md"), (127, "page_127.md")]
)
def test_page_filenames_are_zero_padded(tmp_path: Path, page: int, name: str) -> None:
    paths = DocumentPaths(data_dir=tmp_path, document_hash="h")
    assert paths.page_md(page).name == name
    assert paths.page_error(page).name == name.replace(".md", ".error")


def test_ensure_creates_the_pages_directory(tmp_path: Path) -> None:
    paths = DocumentPaths(data_dir=tmp_path, document_hash="h")
    paths.ensure()
    assert paths.pages_dir.is_dir()


def test_write_text_uses_lf_even_on_windows(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    write_text(target, "one\ntwo\n")
    assert target.read_bytes() == b"one\ntwo\n"


def test_write_text_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "out.md"
    write_text(target, "x")
    assert target.read_text(encoding="utf-8") == "x"


def test_write_json_is_lf_only_and_ends_with_a_newline(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json(target, {"a": 1})
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert read_json(target) == {"a": 1}


def test_write_json_keeps_non_ascii_readable(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_json(target, {"port": "Ngqura — south"})
    assert "—" in target.read_text(encoding="utf-8")


def test_a_failed_write_leaves_the_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.json"
    write_json(target, {"generation": 1})

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_json(target, {"generation": 2})

    assert read_json(target) == {"generation": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_jsonl_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "rows.jsonl"
    append_jsonl(target, {"id": "1"})
    append_jsonl(target, {"id": "2"})
    assert [row["id"] for row in read_jsonl(target)] == ["1", "2"]


def test_read_jsonl_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(read_jsonl(tmp_path / "nope.jsonl")) == []


def test_settings_reject_a_missing_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL_AGENT", "a")
    monkeypatch.setenv("GEMINI_MODEL_EXTRACT", "e")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "GEMINI_MODEL_CLASSIFY" in str(exc.value)


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "GEMINI_API_KEY": "k",
        "GEMINI_MODEL_AGENT": "a",
        "GEMINI_MODEL_EXTRACT": "e",
        "GEMINI_MODEL_CLASSIFY": "c",
    }.items():
        monkeypatch.setenv(name, value)
    settings = load_settings()
    assert settings.gemini_model_classify == "c"
    assert settings.gemini_api_key.get_secret_value() == "k"
    assert settings.ingest_concurrency == 4


def test_settings_never_carry_a_model_name_default() -> None:
    for field in ("gemini_model_agent", "gemini_model_extract", "gemini_model_classify"):
        assert Settings.model_fields[field].is_required()
