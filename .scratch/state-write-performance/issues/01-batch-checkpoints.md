# Batch durable checkpoints at public tool boundaries

Status: in progress
Type: task

## Problem

Production run `run-20261113-180145-46499b77` spent approximately 39 minutes
registering 1194/3326 comparison attempts without starting mdcompare. Every
attempt and result rewrites a roughly 55 MB JSON state. The normal comparison
path performs 2N+1 full writes. Graphify repeats the per-source pattern around
one directory-level subprocess; interruption handling repeats it too.

## Scope

Preserve the JSON schema, atomic replacement/fsync, target locks, exact version
suppression and independent public CLI boundaries. Batch comparison and graph
attempt/results; batch interruption records; combine successful export result
and raw-fact writes. Expose preparation/tool/result phases through status.
Do not modify installed software, production state or the active UAT. SQLite
migration, retry policy and model/tool algorithms are outside this patch.

## Acceptance

- Comparison performs three state writes for both small and larger batches.
- Graphify performs two state writes for success, failure and interruption.
- The public child observes all attempt records persisted before execution.
- Save failure prevents launch; local preparation failure leaves work unstarted.
- Batch completion preserves older blocked versions and unrelated state.
- Interruption persists all unknown results in one write. Later individually
  unstarted export/OCR work remains eligible, covered by existing CLI tests.
- Focused tests, lint, local OCR review and PR CI; no local full make check/smoke.
