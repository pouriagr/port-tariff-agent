"""Hashing and file IO.

Every write goes through here so that output is UTF-8 with LF endings on every platform,
and so that a crash mid-write cannot leave a half-written artifact behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def write_text(path: Path, text: str) -> None:
    """Write atomically: a reader sees either the old file or the complete new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dump(value: Any) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2, by_alias=True)
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def write_json(path: Path, value: Any) -> None:
    write_text(path, _dump(value) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        value.model_dump_json(by_alias=True)
        if isinstance(value, BaseModel)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")


def read_jsonl(path: Path) -> Iterator[Any]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)
