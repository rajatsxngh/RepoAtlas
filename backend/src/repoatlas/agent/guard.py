"""Between a tool and a model (AGT-3, GRD-3, GRD-7): check what goes in, prepare what comes out.

**Going in.** ``check_arguments`` runs after the argument model accepted a call and before the
tool touches the store. It applies the allowlist helpers (``repoatlas.guardrails.allowlist``):
a path must have the shape of a repository path, a symbol key the shape of a key, and free
text must be text. A malformed path is refused as *not allowed*; whether a well-formed path is
a file of the snapshot is decided by the store's own allowlist when the tool reads it.

**Coming out.** A tool's text is repository content: code, names, paths, author names. Before a
model sees it, ``prepare_result``

1. trims it to a token budget, keeping the head and cutting at a line break, with a note saying
   how much was cut and how to get the rest;
2. passes it through the injection detector, wrapping what it marks in
   ``<flagged reason="…">…</flagged>`` and defusing anything that could open or close a prompt
   block (the same marking as evidence in one-shot prompts);
3. appends the evidence list: one ``<evidence id="E7" path="…" lines="10-24"/>`` line per piece
   of code the result is about. The list is never trimmed, so everything a result found stays
   citable even when its text was cut.

Trimming happens before the result is appended to a conversation; nothing already in a
conversation is ever changed.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from pydantic import BaseModel, ConfigDict

from repoatlas.agent.results import ToolError, ToolResult
from repoatlas.answers.prompting import estimate_tokens, mark_flagged
from repoatlas.guardrails.allowlist import path_problem, validate_symbol_key, validate_text
from repoatlas.guardrails.detector import kinds, marked, scan
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.store.errors import InvalidQuery

EVIDENCE_HEADING = "Evidence from this result (cite these ids):"
MIN_RESULT_TOKENS = 200
CHARS_PER_TOKEN = 3.5  # the estimate used for prompts (``estimate_tokens``)
PATH_FIELDS = frozenset({"path"})
KEY_FIELDS = frozenset({"symbol_key", "from_key", "to_key"})
TEXT_FIELDS = frozenset({"query", "name"})
MAX_ERROR_CHARS = 600


def check_arguments(arguments: BaseModel) -> None:
    """Raise ``ToolError`` unless every path, key, and text argument is well formed."""
    for name, value in arguments.model_dump().items():
        if not isinstance(value, str):
            continue
        if name in PATH_FIELDS:
            problem = path_problem(value)
            if problem is not None:
                raise ToolError(
                    f"{value[:200]!r} is not a file of this snapshot: the path {problem}.",
                    allowed=False,
                )
        try:
            if name in KEY_FIELDS:
                validate_symbol_key(value)
            elif name in TEXT_FIELDS:
                validate_text(value, max_chars=len(value), name=name)
        except InvalidQuery as exc:
            hint = " Use find_definition to look a symbol up by name." if name in KEY_FIELDS else ""
            raise ToolError(f"Invalid {name}: {exc}{hint}") from None


class Trimmed(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    lines_shown: int
    lines_total: int

    @property
    def cut(self) -> bool:
        return self.lines_shown < self.lines_total


def trim_text(text: str, max_tokens: int) -> Trimmed:
    """The head of ``text`` that fits ``max_tokens``, cut at a line break. One line longer than
    the whole budget is cut inside the line."""
    lines = text.split("\n")
    if estimate_tokens(text) <= max_tokens:
        return Trimmed(text=text, lines_shown=len(lines), lines_total=len(lines))
    budget = int(max_tokens * CHARS_PER_TOKEN)
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    if not kept:
        return Trimmed(text=lines[0][:budget], lines_shown=0, lines_total=len(lines))
    return Trimmed(text="\n".join(kept), lines_shown=len(kept), lines_total=len(lines))


def cut_note(trimmed: Trimmed, tokens: int) -> str:
    shown = (
        f"the first {trimmed.lines_shown} lines are shown"
        if trimmed.lines_shown
        else "only the start of its first line is shown"
    )
    return (
        f"[Cut to fit: this result had {trimmed.lines_total} lines (about {tokens} tokens) and "
        f"{shown}. Everything it found is still in the evidence list. To see more, ask a "
        "narrower question or read the lines you need with read_file.]"
    )


def mark_text(text: str) -> tuple[str, list[str]]:
    """``text`` with what the injection detector marks wrapped in ``<flagged>`` and prompt
    block markers defused, and the kinds that were marked."""
    flags = marked(scan(text))
    return mark_flagged(text, flags), kinds(flags)


def evidence_line(evidence_id: str, ref: EvidenceRef, flags: Sequence[str] = ()) -> str:
    """One entry of a result's evidence list. The path is escaped, so a file name can't close
    the tag; ``flags`` are the kinds the detector marked in the referenced lines or the path."""
    attrs = (
        f'id="{evidence_id}" path="{escape(ref.path, quote=True)}" '
        f'lines="{ref.start_line}-{ref.end_line}"'
    )
    if flags:
        attrs += f' flags="{",".join(flags)}"'
    return f"<evidence {attrs}/>"


class Prepared(BaseModel):
    """A tool result as a model sees it."""

    model_config = ConfigDict(frozen=True)

    text: str
    truncated: bool  # the tool left something out, or the text was cut here
    flags: list[str]  # kinds the detector marked in the text
    tokens: int  # estimated tokens of ``text``


def prepare_result(
    result: ToolResult, evidence_lines: Sequence[str], *, max_tokens: int
) -> Prepared:
    """Trim, mark, and list the evidence (see the module docstring). ``evidence_lines`` are the
    result's references as ``evidence_line`` entries, in the result's order."""
    if max_tokens < MIN_RESULT_TOKENS:
        raise ValueError(f"A tool result needs at least {MIN_RESULT_TOKENS} tokens.")
    trimmed = trim_text(result.content, max_tokens)
    body, flags = mark_text(trimmed.text)
    parts = [body]
    if trimmed.cut:
        parts.append(cut_note(trimmed, estimate_tokens(result.content)))
    if evidence_lines:
        parts.append("\n".join([EVIDENCE_HEADING, *evidence_lines]))
    text = "\n\n".join(parts)
    return Prepared(
        text=text,
        truncated=result.truncated or trimmed.cut,
        flags=flags,
        tokens=estimate_tokens(text),
    )


def prepare_error(message: str) -> str:
    """An error message as a model sees it. Messages can repeat an argument, and arguments can
    come from repository text, so the message is marked like any other tool output."""
    text, _ = mark_text(message[:MAX_ERROR_CHARS])
    return f"The call failed: {text}"
