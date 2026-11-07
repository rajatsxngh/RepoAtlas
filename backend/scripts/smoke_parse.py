"""Parse pinned real repositories and report inventory and parse-issue statistics (M01 T11).

Usage (from backend/):
    uv run python scripts/smoke_parse.py

Not part of CI: it needs network access. If anonymous GitHub API calls fail or hit the rate limit,
set REPOATLAS_GITHUB_TOKEN first. Exits non-zero if any repo has parse issues in 1% or more of its
parsed files (the M01 exit criterion).
"""

from __future__ import annotations

import sys
import time

from repoatlas.config import get_settings
from repoatlas.pipeline.parse import run_parse

PINNED = [
    ("https://github.com/pallets/flask", "49943b5a94413a530761350f04e896f76e8d620e"),
    ("https://github.com/colinhacks/zod", "19c40d2072288b4cafec851e9d056a72a88e5973"),
]
MAX_ISSUE_RATE = 0.01


def main() -> int:
    settings = get_settings()
    failed = False
    for url, sha in PINNED:
        started = time.perf_counter()
        output = run_parse(url, settings, commit=sha)
        elapsed = time.perf_counter() - started
        inv, stats = output.inventory.stats, output.stats
        rate = stats.files_with_issues / stats.files_parsed if stats.files_parsed else 0.0
        failed |= rate >= MAX_ISSUE_RATE
        print(f"{output.source} @ {sha[:12]}")
        print(f"  tree entries {inv.tree_entries:,}; source files {inv.source_files:,}")
        print(f"  parsed {stats.files_parsed:,} {dict(inv.indexed_by_language)}")
        print(f"  skipped {dict(inv.skipped)}")
        print(
            f"  symbols {stats.symbols:,}; imports {stats.imports:,}; calls {stats.calls:,}; "
            f"tests {stats.tests:,}; routes {stats.routes:,}"
        )
        print(f"  files with parse issues: {stats.files_with_issues} ({rate:.2%})")
        for parsed in output.files:
            if parsed.issue_count:
                first = parsed.issues[0]
                where = f"line {first.range.start_line}" if first.range else "file"
                print(
                    f"    - {parsed.path}: {parsed.issue_count} issue(s), "
                    f"first {first.kind} at {where}"
                )
        print(f"  digest {output.digest}")
        print(f"  time {elapsed:.1f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
