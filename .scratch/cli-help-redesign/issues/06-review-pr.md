# feat: Review and new PR after completion delivery

Type: task
Status: claimed
Blocked by: 05

## Scope

Run Standards and Spec reviews plus installed OCR, fix findings, close tickets, commit/push current integration branch and update user-identified new PR after completion. Do not merge or release.

Spec: [CLI help redesign](../spec.md).

## Acceptance

Meet the relevant spec decisions through the agreed public CLI seam. Record red/green evidence, focused checks, delivered commit or patch, and limitations before resolving.

## Comments

All known review findings are fixed and targeted regressions pass. User clarified
that no PR existed; publish a new PR from the existing integration branch.
Full post-fix check/smoke belongs to PR CI, not another local run.
