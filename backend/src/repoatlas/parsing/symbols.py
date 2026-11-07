"""Stable symbol keys (PRD §7.3, decision 0003).

``<lang>:<path>:<qualname>:<kind>[~<n>]`` — no line numbers, so a symbol keeps its key across
commits as long as its file, enclosing names, and kind stay the same. ``%``, ``:``, and ``~``
inside a part are percent-encoded so keys always split cleanly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

SymbolLang = Literal["py", "ts", "js"]
SymbolKind = Literal["class", "function", "method", "test"]

_LANGS = frozenset(get_args(SymbolLang))
_KINDS = frozenset(get_args(SymbolKind))
_DECODE_RE = re.compile(r"%(25|3A|7E)")
_DECODED = {"25": "%", "3A": ":", "7E": "~"}


@dataclass(frozen=True, slots=True)
class SymbolKeyParts:
    lang: SymbolLang
    path: str
    qualname: str
    kind: SymbolKind
    ordinal: int = 1


def encode_part(text: str) -> str:
    return text.replace("%", "%25").replace(":", "%3A").replace("~", "%7E")


def decode_part(text: str) -> str:
    return _DECODE_RE.sub(lambda match: _DECODED[match.group(1)], text)


def make_symbol_key(
    lang: SymbolLang, path: str, qualname: str, kind: SymbolKind, ordinal: int = 1
) -> str:
    """Build a key; ``ordinal`` > 1 disambiguates same-named siblings in source order."""
    if ordinal < 1:
        raise ValueError("ordinal starts at 1")
    key = f"{lang}:{encode_part(path)}:{encode_part(qualname)}:{kind}"
    return key if ordinal == 1 else f"{key}~{ordinal}"


def parse_symbol_key(key: str) -> SymbolKeyParts:
    """Split a key back into its parts; raises ``ValueError`` for malformed keys."""
    parts = key.split(":")
    if len(parts) != 4:
        raise ValueError(f"Not a symbol key: {key!r}")
    lang, path, qualname, tail = parts
    kind, _, ordinal_text = tail.partition("~")
    if lang not in _LANGS or kind not in _KINDS:
        raise ValueError(f"Not a symbol key: {key!r}")
    ordinal = int(ordinal_text) if ordinal_text else 1
    if ordinal_text and (ordinal < 2 or str(ordinal) != ordinal_text):
        raise ValueError(f"Bad ordinal in symbol key: {key!r}")
    return SymbolKeyParts(
        lang=lang,  # type: ignore[arg-type]
        path=decode_part(path),
        qualname=decode_part(qualname),
        kind=kind,  # type: ignore[arg-type]
        ordinal=ordinal,
    )


class KeyAllocator:
    """Assigns keys within one file, numbering same-named siblings ``~2``, ``~3``… in order."""

    def __init__(self, lang: SymbolLang, path: str) -> None:
        self._lang = lang
        self._path = path
        self._seen: dict[tuple[str, SymbolKind], int] = {}

    def allocate(self, qualname: str, kind: SymbolKind) -> str:
        count = self._seen.get((qualname, kind), 0) + 1
        self._seen[(qualname, kind)] = count
        return make_symbol_key(self._lang, self._path, qualname, kind, count)
