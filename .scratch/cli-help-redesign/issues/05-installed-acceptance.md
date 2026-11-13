# feat: Fresh-install acceptance and packaging

Type: task
Status: resolved
Blocked by: 01, 02, 03, 04, 07

## Scope

Package local full contracts. Extend existing isolated install checks across real entry points, examples and offline side effects. Run required focused and full gates; preserve unrelated local artifacts.

Spec: [CLI help redesign](../spec.md).

## Acceptance

Meet the relevant spec decisions through the agreed public CLI seam. Record red/green evidence, focused checks, delivered commit or patch, and limitations before resolving.

## Comments

User verification boundary: after OCR fixes, full make check/make smoke run
only in PR CI. Prior local installed proof remains scoped to its recorded
source; final-source full acceptance is pending CI.

## Answer

Main PR: https://github.com/proletariat64/dogpdkb/pull/20
Runtime payload: 0cddefd44f070f686d5ef4f4ce5ba814faa94858.
Final focused preservation suite: 102 passed; independent final peer cases: 7 passed.
Full post-OCR quality and isolated-install gates ran only in CI and passed at
07d44bf: push run 43368250538 and PR run 43368302338. Final local full-check
attempt was stopped (130); the latest local full-smoke launch never started.
Skylark implementation/patch/package is delivered locally and with this PR;
separate repository publication remains subject to destination authorization.
