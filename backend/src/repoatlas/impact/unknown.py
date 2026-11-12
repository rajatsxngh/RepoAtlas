"""The unknown bucket (IMP-4): places that may depend on a target but the graph cannot confirm.

- Call sites without an edge (``unresolved_calls``: ambiguous, not found, unbound, or dynamic)
  whose callee's last name segment equals the target's name (``obj.parse()`` for ``parse``).
- String literals whose whole content is the target's name (``getattr(obj, "parse")``, registry
  tables, ``__all__`` lists), found by a text search over the snapshot's files.

Sites inside a changed symbol itself are left out. Unknown sites are listed with their reason;
they are never folded into the ranked items, and never reported as safe.
"""

from __future__ import annotations

from collections.abc import Sequence

from repoatlas.graph.model import Node, NodeKind
from repoatlas.impact.data import ImpactData
from repoatlas.impact.schema import UnknownSite
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.store.errors import InvalidQuery, PathNotInSnapshot

MAX_UNKNOWN = 100  # sites one report lists at most
MAX_LITERAL_SITES = 50
MIN_LITERAL_NAME = 3  # shorter names match too many unrelated strings
NAMED_KINDS = (NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD)


def unknown_sites(
    data: ImpactData,
    snapshot_id: int,
    targets: Sequence[str],
    target_nodes: dict[str, Node],
    *,
    limit: int = MAX_UNKNOWN,
) -> list[UnknownSite]:
    changed = set(targets)
    names = sorted(
        {node.name for key in targets if (node := target_nodes.get(key)) and _named(node)}
    )
    found: dict[tuple[str, int, int, str, str], UnknownSite] = {}

    for name in names:
        calls = [
            call
            for call in data.unresolved_calls(snapshot_id, name)
            if call.callee_text.rsplit(".", 1)[-1] == name and call.caller_key not in changed
        ]
        callers = data.nodes(snapshot_id, sorted({call.caller_key for call in calls}))
        oids = data.blob_oids(snapshot_id, sorted({n.path for n in callers.values()}))
        for call in calls:
            caller = callers.get(call.caller_key)
            oid = oids.get(caller.path) if caller is not None else None
            if caller is None or oid is None:
                continue
            site = EvidenceRef(
                blob_oid=oid,
                path=caller.path,
                start_line=call.site.start_line,
                end_line=max(call.site.end_line, call.site.start_line),
                symbol_key=caller.key,
            )
            key: tuple[str, int, int, str, str] = (
                site.path,
                site.start_line,
                call.site.start_col,
                call.reason,
                call.callee_text,
            )
            found.setdefault(
                key,
                UnknownSite(
                    site=site,
                    callee_text=call.callee_text,
                    candidates=list(call.candidates),
                    reason=call.reason,
                ),
            )

        if len(name) < MIN_LITERAL_NAME:
            continue
        for text_site in data.literal_sites(snapshot_id, name, MAX_LITERAL_SITES):
            try:
                enclosing = data.locate(snapshot_id, text_site.path, text_site.line)
            except (PathNotInSnapshot, InvalidQuery):
                continue
            if enclosing.key in changed:
                continue
            ref = EvidenceRef(
                blob_oid=text_site.blob_oid,
                path=text_site.path,
                start_line=text_site.line,
                end_line=text_site.line,
                symbol_key=enclosing.key,
            )
            key = (ref.path, ref.start_line, -1, "string_literal", text_site.literal)
            found.setdefault(
                key, UnknownSite(site=ref, callee_text=text_site.literal, reason="string_literal")
            )

    ordered = [found[key] for key in sorted(found)]
    return ordered[:limit]


def _named(node: Node) -> bool:
    return node.kind in NAMED_KINDS and bool(node.name)
