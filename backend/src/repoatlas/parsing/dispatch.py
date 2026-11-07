"""Parse one inventoried file into a ``ParsedFile``."""

from __future__ import annotations

from repoatlas.ingest.inventory import FileEntry
from repoatlas.parsing.models import ParsedFile, ParseIssue
from repoatlas.parsing.python import PythonExtractor
from repoatlas.parsing.ts_runtime import MAX_ISSUES_PER_FILE, collect_issues, grammar_for, parse
from repoatlas.parsing.typescript import TypeScriptExtractor

# Bump when parse_file's output changes for the same input (the parse goldens change with it);
# it is part of every snapshot's configuration fingerprint.
EXTRACTOR_VERSION = 1


def parse_file(entry: FileEntry, data: bytes) -> ParsedFile:
    """Extract symbols, imports, calls, and tests; syntax errors are recorded, never raised.

    Ranges index into ``data`` itself. tree-sitter skips a leading UTF-8 byte order mark, and its
    three bytes still count toward the columns of line 1.
    """
    if entry.skip_reason is not None:
        raise ValueError(f"{entry.path} is skipped ({entry.skip_reason}) and is not parsed.")
    issues: list[ParseIssue] = []
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(
            ParseIssue(
                kind="decode_error",
                message="The file is not valid UTF-8; invalid bytes were replaced.",
                range=None,
            )
        )

    tree = parse(grammar_for(entry.path), data)
    extractor: PythonExtractor | TypeScriptExtractor
    if entry.language == "py":
        extractor = PythonExtractor(data, entry.path, is_test=entry.is_test)
    else:
        extractor = TypeScriptExtractor(
            data, entry.path, lang=entry.language, is_test=entry.is_test
        )
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
