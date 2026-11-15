# LinkedIn launch post (draft)

> **Status: a draft for the owner to edit and publish.** Nothing has been posted. Fill the two
> placeholders in angle brackets, and check the three boxes below before publishing.

## Before publishing

- [ ] `<PUBLIC URL>` is live and open (live-pass item 26), and the demo recording shows a real
      cited answer (item 27). Attach that recording, not the local one.
- [ ] The numbers below still match [`docs/results.md`](../results.md). If the final
      evaluation was run (item 25), replace them with its numbers and delete the sentence
      that starts "Not evaluated yet".
- [ ] If the final evaluation was **not** run, keep the "Not evaluated yet" sentence. It is
      what makes the post true.

## The post

I built RepoAtlas: paste a public GitHub URL and get an interactive map of the code, answers
that cite file and line, and a prediction of what breaks if you change a function.

Three rules shaped it:

1. Static analysis builds the graph. The model never draws a box or an arrow.
2. Every claim in an answer cites code, and a checker drops citations that don't hold.
3. The accuracy is measured on a frozen test set and published, including where it loses.

What the numbers say (90 questions over five open-source repos, three runs each, 95%
intervals):

→ Answers with the code graph score 0.570 [0.492, 0.648]; the same model with vector search
alone scores 0.522. The graph adds +0.047 [+0.010, +0.090]. Real, and small.

→ The gain comes from "who calls this?" and "what does this change affect?". On multi-hop
questions the graph added nothing: +0.009 [-0.025, +0.047].

→ "What breaks?" is a graph traversal, not a model call, so it costs $0. Against tests that
really fail when the function is broken in a sandbox, its recall is 0.167 [0.000, 0.389]. Most
real failures arrive through test fixtures a static graph can't see. That's the part I'd work
on next.

→ With no code at all, the model still got 42% of lookup questions right from memory. These
repos are famous, which is why answers are scored on what they cite.

An answer cost about 2 cents. Not evaluated yet: the agent and multi-agent modes I added
afterwards to go after the multi-hop gap. They're in the app, and their numbers will go on
the results page when they exist.

Try it: <PUBLIC URL>
Code, benchmark, and every report: https://github.com/rajatsxngh/RepoAtlas

Credit where it's due: DeepWiki showed how useful a generated view of a repo can be, and
Understand-Anything's work on code knowledge graphs gave me ideas for the map layout, the
test-to-code links, and the benchmark protocol.

`<ATTACH: the 45-second demo recording>`

## Where each number comes from

| In the post | Source |
|---|---|
| 0.570 [0.492, 0.648], 0.522, 90 questions, three runs | `eval/reports/2026-11-12-hybrid_graphrag-test.md`, `2026-11-12-vector_only-test.md` |
| +0.047 [+0.010, +0.090]; multi-hop +0.009 [-0.025, +0.047] | `eval/reports/2026-11-12-compare-hybrid_graphrag-vs-vector_only-test.md` |
| Recall 0.167 [0.000, 0.389] against real failures | `eval/reports/2026-11-13-impact_engine-intervals-test.md` |
| 42% of lookups from memory | `eval/reports/2026-11-12-closed_book-test.md` (lookup 0.417) |
| About 2 cents per answer | `eval/reports/2026-11-12-hybrid_graphrag-test.md` ($0.0220 per question) |

All of them were measured on 2026-11-12 and 2026-11-13, for one-shot answers on the prompt of
that day. A test (`backend/tests/unit/test_launch_posts.py`) fails if this draft's numbers
drift from the reports.

## Shorter variant

I built RepoAtlas: paste a GitHub URL, get a map of the code, answers that cite file and
line, and a "what breaks if I change this?" report.

The part I care about most is that it's measured. On a frozen test set, adding the code graph
to retrieval improved answers by +0.047 [+0.010, +0.090] over vector search alone. Small but
real. On multi-hop questions it added nothing, and impact predictions catch only a sixth of
the tests that really fail. All of it is published, losses included.

Try it: <PUBLIC URL> · Code and results: https://github.com/rajatsxngh/RepoAtlas

Thanks to DeepWiki and Understand-Anything for the ideas this builds on.
