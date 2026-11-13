# fix: Expose mdcompare as a managed command

Type: task
Status: resolved

## Scope

Create mdcompare alongside dogpdkb/mdocr in the selected managed command directory
on installation and upgrade. Protect unrelated commands and remove only owned
links on uninstall. Preserve legacy environments missing this link or executable.
Keep the existing PATH guidance when the command directory is absent from PATH.

## Acceptance

Exercise the Bash installer offline for initial install, upgrade, same-version
reinstall, custom paths and command collisions. Verify mdcompare resolves through
PATH and follows the active environment, with uninstall preserving foreign links.
Run focused checks only; full make check/smoke belong to PR CI.

## Answer

Initial install, upgrade and custom paths now expose mdcompare in the managed
command directory. Both installer preflights protect foreign files/symlinks;
uninstall removes only an exact owned link. Legacy environments without the
mdcompare executable or link remain manageable. The candidate's mdcompare must
run before activation so an incomplete package preserves the old environment.
This host already has ~/.local/bin on PATH; shell configuration edits are not
needed. Future installations retain the existing PATH guidance.

Observed six regression failures before the fix. The focused installer,
lifecycle and help suite passed 87 tests. Shell syntax, Ruff, changed-document
checks and diff checks passed. Full quality/install gates run in PR CI.
