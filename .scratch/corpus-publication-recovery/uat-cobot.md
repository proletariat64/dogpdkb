# Corpus publication recovery UAT

Date: 2026-11-14. Candidate: v0.2.3, based on master 0ec3ff6 (v0.2.2).

## Reproduced failure

A valid 229-byte final basename fails when the original publisher adds the run ID
and temporary suffix. A two-source public CLI reproduction publishes the first
source, fails with ENAMETOOLONG on the second, then rejects its own first write on
resume as a changed baseline.

## co.bot real-material acceptance

- Source: failed run `run-20261113-180145-46499b77`, book `fund-platform`.
- Isolated workspace: `/tmp/dogpdkb-publication-uat-zno0zknj`.
- Candidate wheel extracted into that workspace; existing installed Python and
  dependencies reused. Production installation was unchanged during UAT.
- All 3,326 raw inputs agree with their frozen and retained copies.
- Reused 188 public OCR report outputs and 3,138 retained fallback candidates.
- External one-off recovery reconstructed the legacy run publication handoff only
  in the clone. No automatic historical migration/retry feature was added.
- Forced SIGKILL after replacement: two new corpus files existed without saved
  version facts. The public `apply --resume` reconciled both and finished.
- Final: **3,326/3,326** published files match their witnesses and state versions.
- **94** otherwise-valid near-limit destinations publish successfully.
- Final resume took **10.45 seconds** on this host/workload. This is publication
  recovery timing, excluding material copying and Graphify extraction.
- Zero external tool calls during recovery (configured tool sentinels were unused).
- **4,820** production state/run/corpus/native graph files remain byte-identical.
- Final exit **2**, status **partial**, preserving existing optional OCR failures.
- Large Graphify extraction was not run. An independent real Graphify localhost
  incremental contract probe passed outside sandbox (one sandbox timeout).

The first copy attempt lost its connection and temporary workspace before an
acceptance result. The successful attempt used a detached job, durable task logs
and excluded unrelated historical Graphify caches. It is the evidence above.

## Focused validation

Real CLI regressions cover long Unicode filenames, interrupted publication with
and without saved facts, no OCR re-entry, external edits, symlinks, newer versions,
and reuse of final manifests with pending optional OCR. Existing apply, Graphify,
batch-state, durable-input and publication-preservation checks were exercised.
Full check/smoke gates remain in PR CI and the existing release workflow.

Installed OCR reviewed the runtime change with zero findings. Greptile and CI
results are recorded on the PR. No production resume is part of this UAT.
