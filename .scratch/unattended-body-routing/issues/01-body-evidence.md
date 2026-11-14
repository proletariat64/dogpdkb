# 01: Verified body evidence and isolated comparison failures

Status: resolved
Type: task
Blocked by: None

## What to build

Public mdcompare behavior excludes verified exporter metadata, keeps semantic protections, and prevents unrelated failed baselines from contaminating local candidates.

## Acceptance

- Prove the corresponding spec behaviors through focused public boundaries.
- Preserve production artifacts and unrelated work.
- Do not run local OCR review, OCR CI or full local regression.

## Answer

Implemented on the integration branch with focused public-CLI regressions. See the PR evidence and diagnostic.md; delivery gates remain in ticket 04.
