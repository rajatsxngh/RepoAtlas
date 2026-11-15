# Other channels (optional drafts)

> **Status: drafts for the owner.** Nothing has been posted. The plan is one LinkedIn post
> ([linkedin-post.md](linkedin-post.md)); these exist in case the owner wants them. The same
> rules apply: fill `<PUBLIC URL>`, and quote only numbers that are in
> [`docs/results.md`](../results.md).

## Show HN

**Title:** Show HN: RepoAtlas – a code map with cited answers and published accuracy

Paste a public GitHub URL and RepoAtlas builds a map of the code by static analysis
(tree-sitter, then import, call, inheritance, and test links). On top of the map it answers
questions with file-and-line citations that are checked against the commit, and it predicts
what breaks if you change a function.

I wanted to know whether the graph actually helps, so I built a benchmark with ground truth
from SCIP indexes and from breaking functions in a sandbox to see which tests fail. On the
frozen test split (90 questions, five repos), graph-aware retrieval beats vector search by
+0.047 [+0.010, +0.090]. It does nothing for multi-hop questions, and the impact engine's
recall against real test failures is 0.167 [0.000, 0.389], mostly because tests reach code
through fixtures a static graph can't see. All reports are in the repo.

Agent and multi-agent answering modes are in the app but not evaluated yet.

Try it: <PUBLIC URL> · Code: https://github.com/rajatsxngh/RepoAtlas

Inspired by DeepWiki and Understand-Anything.

## Reddit (r/Python or r/typescript)

**Title:** I built a tool that maps a repo and measures how accurate its answers are

Same body as Show HN, with the first paragraph shortened, and one question for the readers
at the end: "Which repository should I add to the gallery?"

## X

RepoAtlas: paste a GitHub URL → an interactive map of the code, answers that cite file and
line, and "what breaks if I change this?"

Measured on a frozen test set: the code graph adds +0.047 [+0.010, +0.090] over vector
search. Losses published too.

<PUBLIC URL>
