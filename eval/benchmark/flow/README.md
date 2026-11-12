# Writing flow questions

Flow questions ask how something moves through the code ("How does a request reach the
transport?", "What happens when the CLI parses an option?"). Their answers can't be derived from
SCIP, so people write them by reading the code. Each repo has four, in `<repo_id>.md` here.
[`_template.md`](_template.md) shows the format.

## Rules

1. **Read the code itself.** Use GitHub at the pinned commit (links below), or a local checkout
   of that commit. Don't use RepoAtlas (its map, answers, or search) or an LLM that has
   indexed the repo. The benchmark has to stay independent of the system it scores.
2. **Ask about a real path through the code** that a new contributor would want explained. It
   should cross at least two files or three functions, and have one correct answer at this commit.
3. **Must cite 3–8 symbols:** the functions, methods, or classes a correct answer has to point
   at. Order them along the flow. Cite only symbols that make a decision or transform data on
   this path. A pass-through (a method that only calls `super()` here) or a one-line lookup
   belongs in the outline, not the must-cite list, because must-cite recall would penalize a
   correct answer that skips it.
4. **Name symbols by symbol key** (`<lang>:<path>:<qualname>:<kind>`, PRD §7.3). To list a
   file's keys:
   `cd backend && uv run repoatlas bench keys --repo click src/click/core.py`.
   This only names symbols; it doesn't say how they relate. The generator fills in each
   symbol's definition line from SCIP.
5. **The reference outline** is 3–8 numbered steps a reviewer can check against the code. Write
   it in your own words, and quote no more than a few identifiers.
6. **IDs** are `<repo_id>-flow-001` to `-004`. Once the test split is frozen, changing a
   question means a new benchmark version.

## Review

Every file has a second pass before the test split is frozen. The reviewer checks each question
against the code at the pinned commit:

- Is the question answerable from the code alone, with one correct answer?
- Is every must-cite symbol on the path, and is none missing that a correct answer needs?
- Is each outline step true at this commit?

The reviewer fixes the file, or asks the author to, then writes their name in `Reviewed by:`.
`repoatlas bench split --freeze` refuses to freeze while any file still says `pending`.

## Pinned commits

| Repo | Code at the pinned commit |
|---|---|
| click | https://github.com/pallets/click/tree/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1 |
| requests | https://github.com/psf/requests/tree/6e83187b8feb273ed4c6cdab5efd8d54901dfab3 |
| httpx | https://github.com/encode/httpx/tree/b5addb64f0161ff6bfe94c124ef76f6a1fba5254 |
| zod | https://github.com/colinhacks/zod/tree/c7ec94d309ee8acb25113e3db738b3e03816a424 |
| remeda | https://github.com/remeda/remeda/tree/ec4d3f5dad1105c522c83ec4877e04937cb2405e |
