# 0011 — Evidence-referenced citations, validation, and refusals

**Status:** Accepted · **Date:** 2026-11-07

## Context

Models asked to produce file paths and line numbers invent plausible ones. A citation check that only filters bad citations makes "100% of shown citations are valid" true by construction, which says nothing about quality.

## Decision

- Every code pointer uses one format: an evidence reference `(blob_oid, path, start_line, end_line, symbol_key?)` (PRD §7.4) — for retrieval chunks, tool results, citations, and impact evidence.
- The model receives numbered evidence (`E1…En`) and cites IDs, never raw paths or lines; the server maps IDs to references.
- A validator removes citations to IDs that weren't provided, ranges that don't exist, and ranges that don't contain the claimed symbol; claims without a valid citation are dropped.
- Pre-filter citation validity and dropped-claim rate are recorded for every answer and reported in evaluations, together with claim support from a separate checker.
- Refusals have kinds: `evidence` (weak retrieval or no surviving claim), `api` (model refusal), `budget` (limits).

## Consequences

- Shown citations always point at real code; quality is measured before filtering.
- Some answerable questions get refused when evidence is weak; refusal metrics make the trade-off visible.
