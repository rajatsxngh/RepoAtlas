# M13 gate evidence

What was run for the M13 build gate (decision
[0021](decisions/0021-m13-build-gate-injection-live-pass.md)) on 2026-11-15, on Windows 11 with
Postgres in Docker. **No model was called and nothing was spent.** The live injection runs are
pending in [live-pass.md](live-pass.md), items 6 and 7.

## The injection suite, end to end, with stand-ins

```bash
cd backend
REPOATLAS_GATE_EVIDENCE=gate.json uv run pytest tests/integration/security/test_injection_gate.py -q
```

Seven tests passed. The suite ran through the real pieces: the fixture
(`tests/fixtures/repos/injection_lab`, 17 variants) was built into a snapshot by the pipeline,
the registered answer systems retrieved from it and built their prompts, citations were
validated against the stored blobs, and the eval runner scored every output with `canary_leak`
and wrote schema-valid reports. The model was replaced by two scripted stand-ins
(`tests/integration/security/standins.py`).

| Mode | Stand-in | Questions × runs | Variants in the prompts | Leaks found | Score |
|---|---|---|---|---|---|
| `oneshot` | obedient | 15 × 1 | 16 (v01, v02, v04–v17) | all 16 canaries, and 5 markers | 0.000 |
| `oneshot` | careful | 15 × 3 | the same 16 | 0 in 45 answers | 1.000 (1.000, 1.000, 1.000) |
| `impact_explain` | obedient | 5 × 1 | 6 (v01, v02, v07, v08, v11, v17) | all 6 canaries, and 1 marker | 0.000 |
| `impact_explain` | careful | 5 × 3 | the same 6 | 0 in 15 answers | 1.000 (1.000, 1.000, 1.000) |

Reading the table:

- **Every indexed variant reaches a prompt.** The 16 variants in source files all appear in
  one-shot prompts. Impact explanations see fewer, because their evidence is the changed
  symbol's and its dependents' function bodies: 6 variants sit in those.
- **The README variant (v03) is shown not to be indexed.** RepoAtlas indexes source files only,
  so `README.md` never becomes evidence; no prompt of any run contains its canary.
- **The scorer catches obedience on every variant**, each by its own canary, including the
  base64 one (v10), whose canary exists only inside the payload. The markers it also caught:
  the exfiltration link (v06), the file that doesn't exist (v08), "purge_all is safe to delete"
  (v09), the forbidden path from the fake tool call (v12), and a reply in the planted role
  (v13).
- **Every prompt marks the planted text.** All 15 one-shot prompts contain `<flagged>` spans,
  and every answer's output records flagged evidence.
- **A stand-in that answers from the evidence scores zero leaks** on three runs of every
  question, in both modes.

The documented command was also run through the CLI, with the real LLM client over the offline
stand-in of decision 0020 in place of the API:

```text
oneshot on injection/dev: 1.000 [1.000, 1.000]; $0.00 spent this run, $0.00 in all; complete
impact_explain on injection/dev: 1.000 [1.000, 1.000]; $0.00 spent this run, $0.00 in all; complete
```

### What this does not show

The stand-ins are scripts. The gate shows that the fixture reaches the model's input, that the
input marks the planted text, that a leak would be seen, and that the command works. Whether
Claude follows any of the 16 planted instructions is unknown until the live run.

## Detector false positives on the benchmark repositories

```bash
uv run repoatlas guardrails measure --snapshot 19 --snapshot 20 --snapshot 21 --snapshot 22 \
  --snapshot 23 --out ../eval/guardrails/detector-benchmark
```

**0 of 13,200 chunks marked** (click, requests, httpx, remeda, zod at their benchmark commits).
59 chunks carry 134 flags below the marking severity, all ordinary URLs with a query string.
The first run marked 22 chunks (0.167%); two rules were narrowed and this is the second run, so
the number is fitted to these repositories, not held out. Details:
[`eval/guardrails/README.md`](../eval/guardrails/README.md).

Detector recall on the fixture: 17 of 17 variants marked
(`tests/unit/guardrails/test_injection_fixture.py`).

## Traces in the UI, with export on and off

- `npx playwright test traces` — 5 specs: an answer, a refused answer, an impact report, and an
  impact explanation each show their trace (mock mode), and an answer, an impact report, and an
  explanation show theirs against the real API and trace store.
- `tests/integration/observability/test_export.py` — the same ask through the API with OTLP
  export off (nothing is sent, the stored trace is complete), on (one connected trace arrives
  at an OTLP/HTTP collector inside the test: request, SQL, and RepoAtlas steps), and with the
  collector unreachable (the answer still succeeds).

- `npx playwright test flagged` — against the real API, on the injection fixture: the cited
  code is flagged, two of three citations carry the warning icon, the metrics line says
  "9 of 20 code excerpts flagged as instruction-like", and no canary reaches the page (the
  answer is the offline stand-in's).

## Security checklist

`docs/security-checklist.md` has 10 items; `tests/unit/security/test_checklist.py` checks that
each names at least one test and that all 70 named tests exist (60 pytest, 10 web). They all
pass in the runs below.

## Whole-suite runs

| Check | Result |
|---|---|
| `cd backend && uv run poe check` | 1,920 passed, 1 skipped, 7 deselected (live); ruff, mypy (419 files), docs lint pass |
| `uv run pytest -m injection_static` | 224 passed |
| `uv run pytest tests/unit/security tests/integration/security` | 200 passed |
| `cd frontend && npm run lint && npm run typecheck && npm test` | pass; 315 unit tests |
| `npx playwright test` | 18 of 18 (7 against the real API, 11 in mock mode) |
| OpenAPI spec, TypeScript types, report schema | current |
