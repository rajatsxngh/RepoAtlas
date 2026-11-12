"""Reading SCIP indexes (SBX-6) without a protobuf dependency.

SCIP (https://github.com/sourcegraph/scip, ``scip.proto``) is a protobuf message. Only the fields
target sampling needs are decoded; everything else is skipped by wire type:

- ``Index``: 1 ``metadata``, 2 ``documents``
- ``Metadata``: 2 ``tool_info`` (1 name, 2 version), 3 ``project_root``
- ``Document``: 1 ``relative_path``, 2 ``occurrences``, 4 ``language``
- ``Occurrence``: 1 ``range`` (packed int32: 3 or 4 values, 0-based lines), 2 ``symbol``,
  3 ``symbol_roles`` (bit 1 = definition)

``encode_index`` writes the same subset; tests use it to build small fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

DEFINITION_ROLE = 0x1
_VARINT, _I64, _LEN, _I32 = 0, 1, 2, 5


class ScipError(ValueError):
    """The bytes are not a readable SCIP index."""


@dataclass(frozen=True, slots=True)
class Occurrence:
    start_line: int  # 0-based, as in SCIP
    start_char: int
    end_line: int
    end_char: int
    symbol: str
    roles: int

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & DEFINITION_ROLE)

    @property
    def is_local(self) -> bool:
        return self.symbol.startswith("local ")


@dataclass(slots=True)
class Document:
    relative_path: str
    language: str = ""
    occurrences: list[Occurrence] = field(default_factory=list)


@dataclass(slots=True)
class ScipIndex:
    tool_name: str = ""
    tool_version: str = ""
    project_root: str = ""
    documents: list[Document] = field(default_factory=list)

    def path_prefix(self, container_roots: tuple[str, ...] = ("/src", "/tmp/work")) -> str:
        """Prefix that turns a document's path into a repo path (``packages/x/`` for a
        project indexed from ``/src/packages/x`` or its copy ``/tmp/work/packages/x``)."""
        root = self.project_root.removeprefix("file://").rstrip("/")
        for base in container_roots:
            if root.startswith(base.rstrip("/") + "/"):
                return root[len(base.rstrip("/")) + 1 :] + "/"
        return ""


# --------------------------------------------------------------------------------------------
# Wire format
# --------------------------------------------------------------------------------------------


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise ScipError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ScipError("varint too long")


def _fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """(field number, wire type, value) for each field of one message."""
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 0x7
        if wire == _VARINT:
            value, pos = _varint(data, pos)
            yield number, wire, value
        elif wire == _LEN:
            length, pos = _varint(data, pos)
            if pos + length > len(data):
                raise ScipError("truncated field")
            yield number, wire, data[pos : pos + length]
            pos += length
        elif wire == _I64:
            pos += 8
        elif wire == _I32:
            pos += 4
        else:
            raise ScipError(f"unsupported wire type {wire}")


def _packed_int32(value: int | bytes) -> list[int]:
    if isinstance(value, int):  # a single unpacked element
        return [value]
    out = []
    pos = 0
    while pos < len(value):
        item, pos = _varint(value, pos)
        out.append(item - (1 << 64) if item >= 1 << 63 else item)
    return out


def _occurrence(data: bytes) -> Occurrence | None:
    rng: list[int] = []
    symbol = ""
    roles = 0
    for number, _, value in _fields(data):
        if number == 1:
            rng.extend(_packed_int32(value))
        elif number == 2 and isinstance(value, bytes):
            symbol = value.decode("utf-8", "replace")
        elif number == 3 and isinstance(value, int):
            roles = value
    if len(rng) == 3:
        return Occurrence(rng[0], rng[1], rng[0], rng[2], symbol, roles)
    if len(rng) == 4:
        return Occurrence(rng[0], rng[1], rng[2], rng[3], symbol, roles)
    return None


def _document(data: bytes) -> Document:
    document = Document(relative_path="")
    for number, _, value in _fields(data):
        if not isinstance(value, bytes):
            continue
        if number == 1:
            document.relative_path = value.decode("utf-8", "replace")
        elif number == 4:
            document.language = value.decode("utf-8", "replace")
        elif number == 2:
            occurrence = _occurrence(value)
            if occurrence is not None and occurrence.symbol:
                document.occurrences.append(occurrence)
    return document


def read_index(data: bytes) -> ScipIndex:
    index = ScipIndex()
    for number, _, value in _fields(data):
        if not isinstance(value, bytes):
            continue
        if number == 1:
            for m_number, _, m_value in _fields(value):
                if m_number == 2 and isinstance(m_value, bytes):
                    for t_number, _, t_value in _fields(m_value):
                        if isinstance(t_value, bytes) and t_number == 1:
                            index.tool_name = t_value.decode("utf-8", "replace")
                        elif isinstance(t_value, bytes) and t_number == 2:
                            index.tool_version = t_value.decode("utf-8", "replace")
                elif m_number == 3 and isinstance(m_value, bytes):
                    index.project_root = m_value.decode("utf-8", "replace")
        elif number == 2:
            index.documents.append(_document(value))
    return index


# --------------------------------------------------------------------------------------------
# Encoding (test fixtures)
# --------------------------------------------------------------------------------------------


def _enc_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _enc_len(number: int, payload: bytes) -> bytes:
    return _enc_varint(number << 3 | _LEN) + _enc_varint(len(payload)) + payload


def _enc_int(number: int, value: int) -> bytes:
    return _enc_varint(number << 3 | _VARINT) + _enc_varint(value)


def encode_index(index: ScipIndex) -> bytes:
    tool = _enc_len(1, index.tool_name.encode()) + _enc_len(2, index.tool_version.encode())
    out = bytearray(_enc_len(1, _enc_len(2, tool) + _enc_len(3, index.project_root.encode())))
    for document in index.documents:
        body = bytearray(_enc_len(1, document.relative_path.encode()))
        for occ in document.occurrences:
            if occ.start_line == occ.end_line:
                rng = [occ.start_line, occ.start_char, occ.end_char]
            else:
                rng = [occ.start_line, occ.start_char, occ.end_line, occ.end_char]
            packed = b"".join(_enc_varint(v) for v in rng)
            item = _enc_len(1, packed) + _enc_len(2, occ.symbol.encode())
            if occ.roles:
                item += _enc_int(3, occ.roles)
            body += _enc_len(2, item)
        if document.language:
            body += _enc_len(4, document.language.encode())
        out += _enc_len(2, bytes(body))
    return bytes(out)
