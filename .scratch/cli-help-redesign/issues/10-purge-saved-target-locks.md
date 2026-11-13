# fix: Hold saved execution target locks during purge

Type: task
Status: resolved

## Scope

Ownership-only purge validation permits changed execution targets, but currently
locks only the new configured corpus/graph. A surviving external tool can still
hold the old saved target lock after its recorded parent PID exits. Force purge
must honor those locks and retain them through deletion, including all-candidate
preflight. Preserve current locks, ownership, protected paths and force checks.
Reject malformed saved target paths and overlap with selected wash. Do not
recreate missing historical target trees or rewrite saved lock PID contents.

## Acceptance

Public CLI and real external lock-holder regressions for saved corpus/graph,
single/all selection, dead recorded PID, lock release and lock retention during
deletion. Focused checks only; no commit, push or full local gates.

## Answer

Confirmed four failing public CLI reproductions before the fix: with an exited
saved parent PID, changed current targets and a live external holder on either
saved corpus/graph lock, force purge deleted wash. All-selection also deleted an
earlier candidate. Ten malformed/overlapping saved-path cases and a saved lock
symlink also failed before the fix; deletion-boundary contenders demonstrated
the absence of retained saved locks.

Purge now validates absolute saved target paths and rejects overlap with any
selected run, deduplicates against current resolved locks, and acquires all
remaining saved locks before any deletion. ExitStack retains those descriptors
through all deletions and closes them on refusal or filesystem failure. Existing
saved lock contents are untouched; O_NOFOLLOW rejects lock symlinks. Missing
historical directories remain absent, while existing directories receive a
missing lock file so an external writer cannot enter during deletion.

Public CLI regression count is 47 (18 new), including unchanged-target cases,
all-candidate refusal before deletion, successful retry after external release,
and external lock contenders at both real shutil.rmtree boundaries for existing
and newly created saved lock files. Focused purge plus purge-help selection:
49 passed, 28 deselected in 3.95 seconds. Ruff check, Ruff format check and
git diff --check passed. No full local gates, production actions, commit or push.
