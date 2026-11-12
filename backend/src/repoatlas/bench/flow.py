"""Hand-written flow questions (BEN-8): ``eval/benchmark/flow/<repo_id>.md``.

People write these by reading the code on GitHub at the pinned commit, without RepoAtlas. Each
file has a header and one section per question:

```
# <repo_id> flow questions

- Written by: <name>
- Reviewed by: <name, or "pending">

## <repo_id>-flow-001

**Question:** How does …?

**Must cite:**
- `py:src/pkg/mod.py:Func:function`
- …

**Reference outline:**
1. …
```

Must-cite entries are symbol keys; their lines come from SCIP definitions (or, for symbols SCIP
does not define, the parsed symbol's name line), so the author only names symbols.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from repoatlas.bench.schema import FlowTruth, Location, Question

PENDING = "pending"
_HEADER = re.compile(r"^- (Written|Reviewed) by:\s*(.+?)\s*$", re.MULTILINE)
_SECTION = re.compile(r"^## (\S+)\s*$", re.MULTILINE)
_QUESTION = re.compile(r"\*\*Question:\*\*\s*(.+?)\s*$", re.MULTILINE)
_KEY = re.compile(r"^- `([^`]+)`", re.MULTILINE)
_BLOCK = re.compile(r"\*\*(Must cite|Reference outline):\*\*\s*\n(.*?)(?=\n\*\*|\Z)", re.DOTALL)


class FlowDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    must_cite: list[str] = Field(min_length=3, max_length=8)
    reference_outline: str


class FlowFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_id: str
    written_by: str
    reviewed_by: str
    questions: list[FlowDraft]

    @property
    def reviewed(self) -> bool:
        return self.reviewed_by.lower() != PENDING and bool(self.reviewed_by)


def parse_flow_file(repo_id: str, text: str) -> FlowFile:
    """Parse one flow file; raises ``ValueError`` naming the first malformed section."""
    header = {kind: value for kind, value in _HEADER.findall(text.split("\n## ", 1)[0])}
    if "Written" not in header or "Reviewed" not in header:
        raise ValueError(f"{repo_id}: the header needs '- Written by:' and '- Reviewed by:'")
    starts = list(_SECTION.finditer(text))
    drafts = []
    for i, match in enumerate(starts):
        qid = match.group(1)
        body = text[match.end() : starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        question = _QUESTION.search(body)
        blocks = {name: value.strip() for name, value in _BLOCK.findall(body)}
        if question is None or "Must cite" not in blocks or "Reference outline" not in blocks:
            raise ValueError(f"{qid}: needs **Question:**, **Must cite:**, **Reference outline:**")
        if not qid.startswith(f"{repo_id}-flow-"):
            raise ValueError(f"{qid}: IDs in {repo_id}.md must be {repo_id}-flow-NNN")
        try:
            drafts.append(
                FlowDraft(
                    id=qid,
                    text=question.group(1),
                    must_cite=_KEY.findall(blocks["Must cite"]),
                    reference_outline=blocks["Reference outline"],
                )
            )
        except ValueError as exc:
            raise ValueError(f"{qid}: {exc}") from exc
    return FlowFile(
        repo_id=repo_id,
        written_by=header["Written"],
        reviewed_by=header["Reviewed"],
        questions=drafts,
    )


def flow_questions(
    flow: FlowFile,
    *,
    commit: str,
    version: str,
    locate: Callable[[str], Location | None],
) -> list[Question]:
    """Questions from a flow file; ``locate`` turns a must-cite key into its location."""
    out = []
    for draft in flow.questions:
        cites = []
        for key in draft.must_cite:
            location = locate(key)
            if location is None:
                raise ValueError(f"{draft.id}: {key} is not a symbol at the pinned commit")
            cites.append(location)
        review = flow.reviewed_by if flow.reviewed else PENDING
        notes = f"Written by {flow.written_by} reading the code; reviewed by {review}."
        out.append(
            Question(
                id=draft.id,
                benchmark_version=version,
                repo_id=flow.repo_id,
                commit=commit,
                type="flow",
                text=draft.text,
                ground_truth=FlowTruth(must_cite=cites, reference_outline=draft.reference_outline),
                split="dev",
                provenance="human",
                notes=notes,
            )
        )
    return out
