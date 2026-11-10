"""Keyword search over chunks with Postgres full-text search (RET-5).

Code identifiers are split into words: ``getUserById`` → ``get user by id``, ``snake_case_name``
→ ``snake case name``, ``pkg.mod.func`` → ``pkg mod func``; the raw identifier (lower-cased) is
kept as a term too, so an exact identifier in a query matches strongly. Words also get a light
suffix stem (``routes`` → ``route``, ``registered`` → ``register``), on both sides.

A chunk's ``tsv`` is built here, not by Postgres's parser (which would split identifiers its
own way): ``chunk_tsvector`` writes a ``tsvector`` literal with positions, header terms at
weight ``A`` and body terms at ``D``. Queries keep the ``simple`` configuration:
``build_query`` turns a question into an ``or`` of its split, stemmed, non-stopword terms for
``websearch_to_tsquery('simple', …)``, and ``keyword_search`` ranks with ``ts_rank_cd``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import Engine, text

from repoatlas.db.engine import get_engine
from repoatlas.retrieval.chunker import Chunk

KEYWORD_VERSION = 1
MAX_POSITION = 16_383  # Postgres keeps positions up to this
MAX_POSITIONS_PER_TERM = 255
MAX_QUERY_TERMS = 32
RANK_NORMALIZATION = 1  # ts_rank_cd: divide by 1 + log(document length)

_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*|[0-9]+")
_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
STOPWORDS: frozenset[str] = frozenset(
    [
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "should",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)


def split_identifier(identifier: str) -> list[str]:
    """The lower-cased words of one identifier, split at ``_``, ``.``, ``$``, case changes
    (``HTTPServer`` → ``http server``), and between letters and digits."""
    return [part.lower() for part in _PART.findall(identifier)]


def identifiers(value: str) -> list[str]:
    """Identifier-like tokens in ``value``, in order (dotted names stay whole)."""
    return _IDENTIFIER.findall(value)


def stem(word: str) -> str:
    """A light suffix stem: ``-ing``, ``-ed``, and a plural ``-s`` (not ``-ss``)."""
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def terms(value: str) -> Iterator[tuple[int, str]]:
    """``(position, term)`` for every term of ``value``: each identifier (lower-cased) and
    its split, stemmed words share one position, counted from 1."""
    for position, identifier in enumerate(identifiers(value), start=1):
        raw = identifier.lower()
        yield position, raw
        for part in split_identifier(identifier):
            if part != raw:
                yield position, part
            stemmed = stem(part)
            if stemmed != part:
                yield position, stemmed


def split_text(value: str) -> str:
    """``value`` as space-separated terms (what the keyword index sees)."""
    return " ".join(term for _, term in terms(value))


def _literal(lexeme: str) -> str:
    return "'" + lexeme.replace("\\", "\\\\").replace("'", "''") + "'"


def tsvector_literal(header: str, body: str) -> str:
    """A ``tsvector`` literal of header terms (weight A) followed by body terms (weight D)."""
    positions: dict[str, list[str]] = {}
    offset = 0
    for weight, value in (("A", header), ("D", body)):
        last = 0
        for position, term in terms(value):
            last = position
            slots = positions.setdefault(term, [])
            if len(slots) < MAX_POSITIONS_PER_TERM:
                slots.append(f"{min(offset + position, MAX_POSITION)}{weight}")
        offset += last
    return " ".join(
        f"{_literal(term)}:{','.join(dict.fromkeys(slots))}"
        for term, slots in sorted(positions.items())
    )


def chunk_tsvector(chunk: Chunk) -> str:
    return tsvector_literal(chunk.header, chunk.text)


def build_query(question: str) -> str | None:
    """A ``websearch_to_tsquery`` input matching any term of ``question``: stemmed words of
    its identifiers (stopwords left out) and, for camelCase or dotted identifiers, the whole
    identifier. ``None`` when nothing is left to search for."""
    chosen: list[str] = []
    for identifier in identifiers(question):
        parts = split_identifier(identifier)
        if len(parts) > 1 and identifier.isalnum():
            # Postgres's parser would split names with ``_``, ``.``, or ``$``; their words
            # are searched anyway, and the whole name only for camelCase.
            chosen.append(identifier.lower())
        chosen.extend(stem(part) for part in parts if part not in STOPWORDS)
    unique = list(dict.fromkeys(chosen))[:MAX_QUERY_TERMS]
    return " or ".join(unique) if unique else None


@dataclass(frozen=True, slots=True)
class KeywordHit:
    chunk_id: int
    score: float


def keyword_search(
    snapshot_id: int, question: str, *, k: int = 40, engine: Engine | None = None
) -> list[KeywordHit]:
    """The snapshot's ``k`` best chunks for ``question`` by ``ts_rank_cd`` (ties by ID)."""
    query = build_query(question)
    if query is None or k < 1:
        return []
    engine = engine or get_engine()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.id, ts_rank_cd(c.tsv, q, :norm) AS score "
                "FROM chunks c, websearch_to_tsquery('simple', :query) q "
                "WHERE c.snapshot_id = :s AND c.tsv @@ q "
                "ORDER BY score DESC, c.id LIMIT :k"
            ),
            {"s": snapshot_id, "query": query, "k": k, "norm": RANK_NORMALIZATION},
        ).all()
    return [KeywordHit(chunk_id=row.id, score=float(row.score)) for row in rows]
