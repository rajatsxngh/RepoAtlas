"""``repoatlas bench audit``: a seeded sample of generated questions as a checklist (BEN-11).

The sheet gives a person everything needed to check each sampled question against the code on
GitHub at the pinned commit: permalinks to every answer, the reference lines that made each
caller a caller, and every line in the repo that mentions the target's name, each marked as the
definition, an import, a counted call, or something the auditor should look at. Mismatches are
fixed in the generator, never in the data, and the sample is audited again.
"""

from __future__ import annotations

import math
import random
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence

from repoatlas.bench.facts import Facts, import_lines
from repoatlas.bench.generate import reaching_tests
from repoatlas.bench.repos import RepoFiles
from repoatlas.bench.schema import (
    AnswerSetTruth,
    ImpactTruth,
    Location,
    LookupTruth,
    Question,
)

MAX_MENTIONS = 40
MAX_SITES = 3


def sample(questions: Sequence[Question], fraction: float, seed: int) -> list[Question]:
    """``ceil(fraction * n)`` generated (non-flow) questions, spread across types."""
    generated = sorted((q for q in questions if q.type != "flow"), key=lambda q: q.id)
    k = math.ceil(fraction * len(generated))
    by_type: dict[str, list[Question]] = defaultdict(list)
    for q in generated:
        by_type[q.type].append(q)
    r = random.Random(f"{seed}:audit")
    for qtype in sorted(by_type):
        r.shuffle(by_type[qtype])
    order: list[Question] = []
    for i in range(max((len(v) for v in by_type.values()), default=0)):
        order.extend(by_type[t][i] for t in sorted(by_type) if i < len(by_type[t]))
    return sorted(order[:k], key=lambda q: q.id)


class Sheet:
    def __init__(self, url: str, commit: str, files: RepoFiles, facts: Facts) -> None:
        self.url = url.removesuffix(".git").rstrip("/")
        self.commit = commit
        self.files = files
        self.facts = facts
        self.lines: list[str] = []

    def link(self, path: str, line: int, label: str | None = None) -> str:
        text = label or f"{path}:{line}"
        return f"[{text}]({self.url}/blob/{self.commit}/{path}#L{line})"

    def loc(self, location: Location) -> str:
        return f"`{location.symbol_key}` at {self.link(location.path, location.line)}"

    def sites(self, callee: str, caller: str) -> str:
        found = sorted(set(self.facts.call_sites.get((callee, caller), [])))
        shown = ", ".join(self.link(p, n) for p, n in found[:MAX_SITES])
        more = f" (+{len(found) - MAX_SITES} more)" if len(found) > MAX_SITES else ""
        return (shown + more) or "(no direct reference recorded)"

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def mentions(self, key: str) -> None:
        """Every line in the repo that mentions the target's name, classified."""
        definition = self.facts.definitions[key]
        name = definition.name
        word = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])")
        counted = {
            site: caller
            for (callee, caller), sites in self.facts.call_sites.items()
            if callee == key
            for site in sites
        }
        rows = []
        for path in self.files.paths():
            if not path.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts")):
                continue
            text = self.files.text(path)
            if name not in text:
                continue
            imports = import_lines(path, text)
            for number, line in enumerate(text.splitlines(), start=1):
                if not word.search(line):
                    continue
                if (path, number) == (definition.path, definition.line):
                    kind = "definition"
                elif (path, number) in counted:
                    kind = f"counted: caller `{counted[(path, number)]}`"
                elif number in imports:
                    kind = "import (not a call)"
                else:
                    kind = "not counted (check)"
                shown = line.strip()[:90].replace("|", r"\|").replace("`", "'")
                rows.append(f"| {self.link(path, number)} | {kind} | `{shown}` |")
        self.add(f"Every line that mentions `{name}` ({len(rows)}):")
        self.add()
        self.add("| Line | Classified as | Text |")
        self.add("|---|---|---|")
        for row in rows[:MAX_MENTIONS]:
            self.add(row)
        if len(rows) > MAX_MENTIONS:
            self.add(f"| … | {len(rows) - MAX_MENTIONS} more | |")
        self.add()


def render_question(q: Question, sheet: Sheet) -> None:
    truth = q.ground_truth
    sheet.add(f"## {q.id} ({q.type}, {q.split}, {q.provenance})")
    sheet.add()
    sheet.add(f"**Question:** {q.text}")
    sheet.add()
    if q.notes:
        sheet.add(f"**Notes:** {q.notes}")
        sheet.add()
    if isinstance(truth, LookupTruth):
        sheet.add(f"**Answer:** {sheet.loc(truth.definition)}")
        sheet.add()
        name = sheet.facts.definitions[truth.definition.symbol_key].name
        same = [d for d in sheet.facts.definitions.values() if d.name == name]
        sheet.add(f"Definitions named `{name}` in the repo: {len(same)}.")
        sheet.add()
        sheet.add("Check: the linked line defines the symbol the question names.")
    elif isinstance(truth, AnswerSetTruth) and q.notes.startswith("template: owner-modules"):
        sheet.add("**Answer (modules):**")
        sheet.add()
        for a in truth.answers:
            sheet.add(f"- {sheet.link(a.path, 1, a.path)}")
        sheet.add()
        sheet.add(
            "Check: each owned file's history (`git log --no-merges --format=%an -- <file>` at "
            "the pinned commit) shows the author with more than half of the commits, and each "
            "module uses a symbol defined in an owned file; no other non-test module does."
        )
    elif isinstance(truth, AnswerSetTruth) and q.type == "multihop":
        target = _target_key(q, sheet.facts)
        sheet.add(f"**Target:** {sheet.loc(sheet.facts.location(target))}")
        sheet.add()
        sheet.add("**Answer (tests), with the path to the target:**")
        sheet.add()
        direct = sheet.facts.callers.get(target, set())
        for a in truth.answers:
            if a.symbol_key in direct:
                via = "calls it directly at " + sheet.sites(target, a.symbol_key)
            else:
                middles = sorted(
                    m
                    for m in direct
                    if not sheet.facts.is_test(m) and a.symbol_key in sheet.facts.callers.get(m, ())
                )
                m = middles[0]
                via = (
                    f"calls `{m}` at {sheet.sites(m, a.symbol_key)}, which calls it at "
                    f"{sheet.sites(target, m)}"
                )
            sheet.add(f"- {sheet.loc(a)}: {via}")
        sheet.add()
        assert reaching_tests(sheet.facts, target) == [a.symbol_key for a in truth.answers]
        sheet.add("Check: each path is real; no other test reaches the target in two steps.")
    elif isinstance(truth, AnswerSetTruth):
        target = _target_key(q, sheet.facts)
        sheet.add(f"**Target:** {sheet.loc(sheet.facts.location(target))}")
        sheet.add()
        sheet.add("**Answer (callers), with the reference lines:**")
        sheet.add()
        for a in truth.answers:
            sheet.add(f"- {sheet.loc(a)}: {sheet.sites(target, a.symbol_key)}")
        sheet.add()
        sheet.mentions(target)
        sheet.add(
            "Check: every counted line is a non-import reference inside its caller, and every "
            "line marked 'not counted' is not a call to this target (a different symbol with "
            "the same name, a comment, a string, or module-level code)."
        )
    elif isinstance(truth, ImpactTruth):
        target = truth.target.symbol_key
        sheet.add(f"**Target:** {sheet.loc(truth.target)}")
        sheet.add()
        callers = sorted(sheet.facts.callers.get(target, ()))
        sheet.add(
            f"**Static set:** {len(truth.static)} definitions within 3 caller hops. "
            f"The {len(callers)} direct callers, with the reference lines:"
        )
        sheet.add()
        for k in callers:
            sheet.add(f"- {sheet.loc(sheet.facts.location(k))}: {sheet.sites(target, k)}")
        sheet.add()
        if truth.real_failing_tests is not None:
            tests = truth.real_failing_tests
            sheet.add(f"**Real failing tests** (sandbox mutation): {len(tests)}, first 10:")
            sheet.add()
            for t in tests[:10]:
                sheet.add(f"- `{t.test_id}` → `{t.symbol_key}`")
            sheet.add()
        sheet.mentions(target)
        sheet.add(
            "Check: the direct callers are right (as for callers questions); deeper levels "
            "apply the same rule to each caller. Real failures come from the sandbox records."
        )
    else:
        quoted = q.text.split("`")[1] if "`" in q.text else None
        if quoted and q.provenance == "scip":
            leaf = quoted.rsplit(".", 1)[-1]
            hits = [p for p in sheet.files.paths() if leaf in sheet.files.text(p)]
            sheet.add(f"Files at the pinned commit that contain `{leaf}`: {len(hits)}.")
            sheet.add()
            sheet.add(f"Check: `git grep -n {leaf} {sheet.commit[:12]}` prints nothing.")
        else:
            sheet.add("Check: nothing in the repository could answer this; a refusal is right.")
    sheet.add()
    sheet.add("- [ ] Matches the code at the pinned commit")
    sheet.add("- Auditor notes:")
    sheet.add()


def _target_key(q: Question, facts: Facts) -> str:
    """The definition a callers or tests-reach question is about (name plus path)."""
    name = q.text.split("`")[1]
    path = q.text.split("`")[3]
    found = [
        d.key
        for d in facts.definitions.values()
        if d.path == path and (d.qualname == name or d.name == name)
    ]
    if len(found) != 1:
        raise ValueError(f"{q.id}: cannot find the target {name} in {path}")
    return found[0]


def render_sheet(
    questions: Sequence[Question],
    sampled: Sequence[Question],
    *,
    fraction: float,
    seed: int,
    sheets: Mapping[str, Sheet],
) -> str:
    generated = sum(q.type != "flow" for q in questions)
    version = questions[0].benchmark_version if questions else "?"
    head = [
        f"# Benchmark {version} audit",
        "",
        f"A seeded sample (`--sample {fraction} --seed {seed}`) of {len(sampled)} of the "
        f"{generated} generated questions (flow questions are reviewed separately). Check each "
        "one against the code on GitHub at the pinned commit; links point there. When one is "
        "wrong, fix the generator, regenerate, and audit again.",
        "",
        "| Question | Type | Verdict |",
        "|---|---|---|",
        *[f"| {q.id} | {q.type} | pending |" for q in sampled],
        "",
    ]
    body: list[str] = []
    for q in sampled:
        sheet = sheets[q.repo_id]
        sheet.lines = []
        render_question(q, sheet)
        body.extend(sheet.lines)
    return "\n".join(head + body).rstrip() + "\n"
