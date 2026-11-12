# Calibration recipes

Repos outside the benchmark, used only to calibrate edge confidences (EVL-10, decision 0013):
their SCIP indexes are compared with RepoAtlas's edges by `repoatlas eval calibrate-edges`.
They are not in `sandbox/recipes/`, so the benchmark never generates questions from them.
Only `repoatlas sandbox build` and `repoatlas sandbox scip` run for them (no tests, no
mutations); like every recipe, they run only in the hardened sandbox.
