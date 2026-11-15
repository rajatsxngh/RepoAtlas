# Demo script (45 seconds)

One take, no voice-over, captions burned in. The viewer should see three things: a URL turns
into a map, the map answers "what breaks?", and an answer points at the exact lines.

## The full demo (to record on the public URL)

> **Status: not recorded.** It needs a live model answer and a public URL, so it is live-pass
> item 27 in [`docs/live-pass.md`](../live-pass.md).

| Time | On screen | Caption |
|---|---|---|
| 0:00–0:05 | The landing page. Paste `https://github.com/pallets/click`, press **Map it** | Paste a public GitHub URL |
| 0:05–0:12 | The gallery card opens the map at once (the repo is precomputed). Top-level folders appear; open `src`, then `click`, then `core.py` | A map built from the code itself: no model draws it |
| 0:12–0:18 | Search for `Context.invoke`; the map centres on it. Click it: the panel shows its code, callers, and owners | Every box opens to its source |
| 0:18–0:27 | Press **What breaks?** The ranked list fills in; hover a result and its path lights up on the map | "What breaks if I change this?" follows calls, imports, and tests |
| 0:27–0:38 | Ask: "How does a command's callback get its parameters?" The answer appears with numbered citations; click one and the cited lines open, highlighted | Answers cite file and line, and every citation is checked |
| 0:38–0:45 | The results table from the README, then the URL | Accuracy is measured and published, losses included |

Rules for the recording:

- 1440 × 900, light theme, browser chrome hidden, cursor visible.
- The answer on screen must be written by the live model. If the trace panel says the offline
  stand-in answered, stop: that take must not be used.
- Use a repository from the gallery so nothing waits for a build.
- Nothing on screen may claim a number that is not in [`docs/results.md`](../results.md).

## The recording that exists now

[`demo.gif`](demo.gif) was recorded on the local stack on 2026-11-16, with no model involved.
It covers the scenes above that need none:

1. the landing page with the precomputed gallery;
2. a gallery card opening its map without a build;
3. folders opening into files and symbols;
4. search, and a symbol's panel with its code;
5. **What breaks?**: the ranked report and the path on the map.

It does **not** show a question being answered, because an answer needs the live model and
the build spends nothing (decision
[0025](../decisions/0025-m17-launch-build-gate-live-pass.md)). No stand-in answer appears in
it.

To record it again (Postgres running, the gallery precomputed, nothing else on the ports):

```bash
node scripts/dev.mjs
cd frontend && node scripts/record-demo.mjs --base-url http://localhost:3000 --out ../docs/launch
```

The script drives the app with Playwright, saves a video, and converts it to
`docs/launch/demo.gif` with `ffmpeg` (which must be on the PATH).
