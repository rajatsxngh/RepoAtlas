"""Configuration fingerprints: which code and settings produced a snapshot (decision 0007).

A snapshot is identified by (repo, commit, fingerprint). The fingerprint is a sha256 over the
build's ordered steps, the versions of the components each step runs (tree-sitter and grammar
packages, the extractors, the graph builder, and the tier table), and the settings that change
their output (caps and history depth). Changing any of them gives a new snapshot instead of
silently mixing output of two pipelines. Settings that only affect speed or access (timeouts,
tokens, URLs, logging) are left out.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from repoatlas.canonical import digest


def compute(parts: Mapping[str, JsonValue]) -> str:
    """sha256 (hex) of the canonical JSON of ``parts``: equal parts, equal fingerprint."""
    return digest(dict(parts))
