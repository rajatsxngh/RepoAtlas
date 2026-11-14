# Guardrail measurements

## Injection detector on the benchmark repositories

[`detector-benchmark.md`](detector-benchmark.md) (and `.json`) is the detector's false-positive
measurement (GRD-3): how many chunks of the five benchmark repositories it would mark in a
prompt. Those repositories contain no planted instructions, so every marked chunk is a false
positive.

Reproduce it with the benchmark snapshots built for the evaluation (their IDs depend on the
database):

```bash
cd backend
uv run repoatlas guardrails measure --snapshot <click> --snapshot <requests> --snapshot <httpx> \
  --snapshot <remeda> --snapshot <zod> --out ../eval/guardrails/detector-benchmark
```

No model is called.

### How to read the number

- **2026-11-15, detector version 1: 0 of 13,200 chunks marked.** 59 chunks carry 134 flags below
  the marking severity (0.5), all `exfiltration_url` at 0.3 or 0.4: ordinary URLs with a query
  string in HTTP libraries' tests.
- **The rules were tuned on these same repositories.** The first run marked 22 of 13,200 chunks
  (0.167%): 15 from a rule that treated "use this function with…" as a request to call a tool,
  and 7 from URLs whose query parameter was named `q`, `query`, or `key`, or sat next to the
  word "request". Both rules were narrowed, and the second run is the one recorded. So 0% is a
  fitted number, not a held-out one: expect a small non-zero rate on other code, especially on
  repositories about language models, where phrases like "system prompt" are ordinary.
- A false positive costs little: the text is still shown to the model, wrapped in a
  `<flagged>` element, and the citation gets a warning icon. A miss costs nothing by itself
  either: the detector is a signal, and the prompt layout treats all repository text as data
  whether or not it is flagged.
