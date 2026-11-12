# Edge precision on benchmark repos (not fitted)

Repos: click @ cf19179e8e2f (call recall 0.450, 4 mis-resolved SCIP references ignored), requests @ 6e83187b8feb (call recall 0.755, 0 mis-resolved SCIP references ignored), httpx @ b5addb64f016 (call recall 0.612, 712 mis-resolved SCIP references ignored), remeda @ ec4d3f5dad11 (call recall 0.927, 0 mis-resolved SCIP references ignored), zod @ c7ec94d309ee (call recall 0.396, 4 mis-resolved SCIP references ignored)

Precision = confirmed / judged, with 95% Wilson intervals. Unjudged edges touch a file SCIP did not index or a symbol SCIP has no definition for.

| Edges | By | Group | Edges | Judged | Confirmed | Precision [95% CI] | Current |
|---|---|---|---|---|---|---|---|
| calls | provenance | name_match | 21752 | 14254 | 8795 | 0.617 [0.609, 0.625] | - |
| calls | tier | T1 | 8682 | 4598 | 4361 | 0.948 [0.942, 0.954] | 0.950 |
| calls | tier | T2 | 5014 | 4161 | 2847 | 0.684 [0.670, 0.698] | 0.900 |
| calls | tier | T3 | 480 | 471 | 468 | 0.994 [0.981, 0.998] | 0.850 |
| calls | tier | T4 | 64 | 62 | 62 | 1.000 [0.942, 1.000] | 0.800 |
| calls | tier | T5 | 2829 | 1195 | 488 | 0.408 [0.381, 0.436] | 0.500 |
| calls | tier | T6 | 4683 | 3767 | 569 | 0.151 [0.140, 0.163] | 0.300 |
| imports | provenance | import_resolver | 2807 | 1058 | 758 | 0.716 [0.689, 0.743] | 1.000 |
| tested_by | provenance | convention | 229 | 37 | 34 | 0.919 [0.787, 0.972] | 0.600 |
| tested_by | provenance | import_resolver | 748 | 226 | 149 | 0.659 [0.595, 0.718] | 0.900 |
| tested_by | provenance | name_match | 13361 | 7827 | 3741 | 0.478 [0.467, 0.489] | - |

Measured only: these repos are not used for fitting.
