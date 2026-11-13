# feat: mdcompare help and repairable diagnostics

Type: task
Status: resolved
Blocked by: none

## Scope

Implement the spec for mdcompare only. Own src/mdcompare and tests/test_mdcompare_help.py. Add scoped short/long/full local help, complete manifests and reports, concrete INVALID_ARGUMENTS diagnostics, and public CLI regressions. Preserve algorithms and exit meanings.

Spec: [CLI help redesign](../spec.md).

## Acceptance

Meet the relevant spec decisions through the agreed public CLI seam. Record red/green evidence, focused checks, delivered commit or patch, and limitations before resolving.

## Comments

## Answer

Merged 3526477 via c36d30a; 143 mdcompare regressions passed, including 12 help cases.
