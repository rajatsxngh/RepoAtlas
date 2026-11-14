# Injection detector on the benchmark repositories

Detector version 1. A chunk is *marked* when it has a flag of severity 0.5 or more: that is what a prompt and the UI would show with a warning. These repositories have no planted instructions, so every marked chunk is a false positive.

**Marked chunks: 0 of 13200 (0.000%).**

| Repository | Commit | Chunks | With any flag | Marked | Marked rate | Marked paths |
|---|---|---|---|---|---|---|
| pallets/click | `cf19179e8e2f` | 2110 | 0 | 0 | 0.000% | 0 of 86 |
| psf/requests | `6e83187b8feb` | 841 | 12 | 0 | 0.000% | 0 of 36 |
| encode/httpx | `b5addb64f016` | 1301 | 29 | 0 | 0.000% | 0 of 57 |
| remeda/remeda | `ec4d3f5dad11` | 3716 | 6 | 0 | 0.000% | 0 of 659 |
| colinhacks/zod | `c7ec94d309ee` | 5232 | 12 | 0 | 0.000% | 0 of 503 |

## Flags by kind (all severities / marked)

| Repository | Kind | Flags | Marked |
|---|---|---|---|
| psf/requests | `exfiltration_url` | 28 | 0 |
| encode/httpx | `exfiltration_url` | 58 | 0 |
| remeda/remeda | `exfiltration_url` | 8 | 0 |
| colinhacks/zod | `exfiltration_url` | 40 | 0 |

## Marked flags (the false positives)

None.
