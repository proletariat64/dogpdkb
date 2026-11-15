# 03: Integrated verification and Greptile

Status: claimed
Type: task
Blocked by: 01, 02

Integrate both slices, run focused checks, open/update the PR, pass ordinary CI and latest-head Greptile 5/5. Fix valid findings with focused regressions. Do not run local OCR review, OCR CI or full local regression. Leave release/deployment outside this feature task.

## Progress

Runtime and documentation slices are integrated in PR #27. The 21 new public CLI regressions pass, the corrected serial scenarios and related state checks pass, changed Python Ruff checks pass, and the integrated document guard passes for 156 documents. Installed sync/apply help exposes --concurrency {1,2,3}. Ordinary CI and Greptile acceptance remain pending.
