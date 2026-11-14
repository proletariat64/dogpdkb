# 05: Replanned retained sources block their owning recovery

Status: claimed
Type: bug

## Observed failure

Production acceptance of v0.2.5 completed explicit saved-run reselection, then stopped before native extraction. The revised plan contains 832 compile, 1,649 retain, 48 reuse and 797 defer decisions. A previously attempted source now routed to retain or defer still triggers the whole-directory same-version guard despite explicit recovery of its owning run.

## Required outcome

Preserve same-version suppression, immutable full-source witnesses and truthful graph completion. Explicit recovery must account for the original owning attempt and the proven body actually scanned after reselection. It must not silently authorize unrelated failed runs, submit uncertain current bodies, discard graph records or mutate production state outside official CLI commands.

## Acceptance

- Reproduce the owning-run retained-source conflict through public CLI boundaries and add negative coverage for unrelated suppressed attempts.
- Keep the fix in the responsible graph integration layer, without a new retry engine or native artifact fabrication.
- Focused checks locally; ordinary full PR CI; Greptile latest-head 5/5; isolated co.bot selective acceptance before merge/release.
- Publish the patch, update both hosts and resume the original production task using official dogpdkb in tmux. No local OCR review or OCR CI.

## Implementation evidence

- The current production blocker is a deferred new source without a prior graph witness. A separate retained-source suppression record was historical; the existing day-versus-instant version distinction remains unchanged.
- Read-only metadata confirms 35 deferred sources lack old-body witnesses, with zero ownership in either public native manifest or graph source records. Other unselected rows have witness metadata.
- The fix prepares omissions only after native ownership absence proof, preserves the original files and graph facts, and restricts same-version recovery to the owning run's explicit authorization. Ordinary and foreign-run suppression remain.
- 66 nearby graph CLI/state tests pass, including 11 new public CLI scenarios. Ruff and diff checks pass. Latest-head Greptile and isolated installed-wheel upgrade acceptance remain pending.
