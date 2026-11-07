"""Canonical JSON and digests for outputs that are compared across runs and machines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def to_data(obj: BaseModel | dict[str, Any] | list[Any]) -> Any:
    return obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj


def canonical_json(obj: BaseModel | dict[str, Any] | list[Any]) -> str:
    """Compact JSON with sorted keys and literal Unicode: identical bytes for identical data."""
    return json.dumps(to_data(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj: BaseModel | dict[str, Any] | list[Any]) -> str:
    """sha256 of the canonical JSON."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def pretty_json(obj: BaseModel | dict[str, Any] | list[Any]) -> str:
    """Readable JSON (sorted keys, two-space indent, trailing newline) for files people review."""
    return json.dumps(to_data(obj), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def write_text_lf(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings on every OS (plain write_text uses CRLF on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
