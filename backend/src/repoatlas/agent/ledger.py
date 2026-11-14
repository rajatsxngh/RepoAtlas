"""The evidence ledger (AGT-5): every piece of code a tool returned, under a stable ID.

Each ``EvidenceRef`` a tool result carries is added once and gets the next ID (``E1``, ``E2``,
…); the same lines returned again keep their ID. An entry holds the referenced lines, read from
the stored blob, and what the injection detector marked in them and in the file's path, exactly
like numbered evidence in a one-shot prompt. The final answer may cite only ledger IDs, and
``CitationValidator`` checks them against ``Ledger.evidence`` the same way it checks one-shot
citations.

A reference whose blob is not stored, or whose lines are past the blob's end, is not added: it
could never be cited validly.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

from repoatlas.agent.guard import evidence_line
from repoatlas.answers.schema import NumberedEvidence
from repoatlas.answers.validate import BlobSource
from repoatlas.guardrails.detector import marked, scan, scan_path
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import EvidenceItem
from repoatlas.store.blobs import split_lines

_Key = tuple[str, str, int, int]


def _key(ref: EvidenceRef) -> _Key:
    return (ref.path, ref.blob_oid, ref.start_line, ref.end_line)


class Ledger:
    def __init__(self, blobs: BlobSource) -> None:
        self.blobs = blobs
        self._by_key: dict[_Key, NumberedEvidence] = {}
        self._lock = threading.Lock()  # tools of one step may run in different threads

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def evidence(self) -> dict[str, NumberedEvidence]:
        """Every entry by ID, in the order the IDs were given."""
        return {entry.id: entry for entry in self._by_key.values()}

    @property
    def flagged(self) -> int:
        return sum(1 for entry in self._by_key.values() if entry.flags or entry.path_flags)

    def add(self, refs: Iterable[EvidenceRef]) -> list[NumberedEvidence]:
        """The entries for ``refs``, in order; new ones get the next IDs."""
        entries: list[NumberedEvidence] = []
        for ref in refs:
            with self._lock:
                known = self._by_key.get(_key(ref))
            if known is not None:
                entries.append(known)
                continue
            text = self._text(ref)
            if text is None:
                continue
            with self._lock:
                entry = self._by_key.get(_key(ref))
                if entry is None:
                    entry = NumberedEvidence(
                        id=f"E{len(self._by_key) + 1}",
                        item=EvidenceItem(
                            ref=ref,
                            text=text,
                            score=0.0,
                            sources=set(),
                            node_key=ref.symbol_key or f"file:{ref.path}",
                        ),
                        flags=tuple(marked(scan(text))),
                        path_flags=tuple(marked(scan_path(ref.path))),
                    )
                    self._by_key[_key(ref)] = entry
            entries.append(entry)
        return entries

    def lines(self, entries: Iterable[NumberedEvidence]) -> list[str]:
        """``entries`` as the evidence list of a tool result."""
        return [evidence_line(entry.id, entry.ref, entry.flag_kinds) for entry in entries]

    def _text(self, ref: EvidenceRef) -> str | None:
        blob = self.blobs.text(ref.blob_oid)
        if blob is None:
            return None
        lines = split_lines(blob)
        if ref.end_line > len(lines):
            return None
        return "\n".join(lines[ref.start_line - 1 : ref.end_line])
