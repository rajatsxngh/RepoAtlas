"""Parse one inventoried file into a ``ParsedFile``."""

from __future__ import annotations

from repoatlas.ingest.inventory import FileEntry
from repoatlas.parsing.models import ParsedFile, ParseIssue
from repoatlas.parsing.python import PythonExtractor
from repoatlas.parsing.ts_runtime import MAX_ISSUES_PER_FILE, collect_issues, grammar_for, parse

_BOM = b"\xef\xbb\xbf"


def parse_file(entry: FileEntry, data: bytes) -> ParsedFile:
    """Extract symbols, imports, calls, and tests; syntax errors are recorded, never raised."""
    if entry.skip_reason is not None:
        raise ValueError(f"{entry.path} is skipped ({entry.skip_reason}) and is not parsed.")
    source = data.removeprefix(_BOM)
    issues: list[ParseIssue] = []
    try:
        source.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(
            ParseIssue(
                kind="decode_error",
                message="The file is not valid UTF-8; invalid bytes were replaced.",
                range=None,
            )
        )

    tree = parse(grammar_for(entry.path), source)
    if entry.language == "py":
        extractor = PythonExtractor(source, entry.path, is_test=entry.is_test)
    else:  # pragma: no cover - TypeScript/JavaScript extraction lands next
        raise NotImplementedError(f"No extractor for {entry.language} yet.")
    symbols, imports, calls, bases, routes, tests, exports = extractor.run(tree.root_node)

    syntax_issues, syntax_total = collect_issues(tree.root_node)
    issues.extend(syntax_issues)
    return ParsedFile(
        path=entry.path,
        language=entry.language,
        blob_oid=entry.blob_oid,
        symbols=symbols,
        imports=imports,
        calls=calls,
        class_bases=bases,
        routes=routes,
        tests=tests,
        exports=exports,
        issues=issues[:MAX_ISSUES_PER_FILE],
        issue_count=syntax_total + (len(issues) - len(syntax_issues)),
    )
