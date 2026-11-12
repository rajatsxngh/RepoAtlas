"""Evaluation: score answer and impact systems on question suites (PRD EVL-1 to EVL-11).

- ``registry`` — answer systems, impact systems, and question suites, registered by name;
- ``outputs`` — a system's output in the form scorers read (cited symbol keys, refusals);
- ``scorers`` — per-question metrics and aggregates;
- ``runner`` — ``repoatlas eval run``: caching, cost cap, concurrency, resume;
- ``stats`` and ``report`` — paired bootstrap intervals, reports, comparisons;
- ``support`` and ``judge`` — the claim-support checker and the flow judge.
"""
