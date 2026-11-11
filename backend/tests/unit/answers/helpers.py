"""Evidence and blobs for answer tests, without a database."""

from __future__ import annotations

import hashlib

from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import EvidenceItem
from repoatlas.store.blobs import line_count, split_lines

APP = '''"""A tiny app."""


def create_app(config):
    """Build the app."""
    app = App(config)
    app.register(routes)
    return app


class App:
    def __init__(self, config):
        self.config = config

    def register(self, routes):
        for route in routes:
            self.add_route(route)
'''

UTIL = """def slugify(text):
    return text.lower().replace(" ", "-")
"""

FILES = {"src/app.py": APP, "src/util.py": UTIL}


def oid(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


BLOBS = {oid(text): text for text in FILES.values()}


def item(
    path: str, start: int, end: int, *, score: float = 0.5, key: str | None = None
) -> EvidenceItem:
    text = FILES[path]
    lines = split_lines(text)
    return EvidenceItem(
        ref=EvidenceRef(
            blob_oid=oid(text), path=path, start_line=start, end_line=end, symbol_key=key
        ),
        text="\n".join(lines[start - 1 : end]),
        score=score,
        sources={"keyword"},
        node_key=key or f"file:{path}",
    )


class FakeBlobs:
    """Blob line counts and text by OID, like the store's blobs table."""

    def __init__(self, blobs: dict[str, str] | None = None) -> None:
        self.blobs = dict(BLOBS if blobs is None else blobs)

    def line_count(self, blob_oid: str) -> int | None:
        text = self.blobs.get(blob_oid)
        return None if text is None else line_count(text)

    def read_lines(self, blob_oid: str, start: int, end: int) -> str | None:
        text = self.blobs.get(blob_oid)
        if text is None:
            return None
        return "\n".join(split_lines(text)[start - 1 : end])


CREATE_APP = item("src/app.py", 4, 9, score=0.9, key="py:src/app.py:create_app:function")
APP_CLASS = item("src/app.py", 12, 18, score=0.6, key="py:src/app.py:App:class")
SLUGIFY = item("src/util.py", 1, 2, score=0.3, key="py:src/util.py:slugify:function")
EVIDENCE = [CREATE_APP, APP_CLASS, SLUGIFY]
