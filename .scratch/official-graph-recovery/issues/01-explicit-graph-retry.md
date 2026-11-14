# fix: recover a diagnosed graph failure through the public CLI

Type: task
Status: claimed

Production evidence: a failed provider route produced connection errors; after
interruption, `update-graph` returned 2 with SAME_VERSION_NOT_RETRIED. There was
no public recovery command after fixing the underlying cause.

Root cause: unconditional same-version suppression has no explicit operator
recovery seam. Partial sync also cleans its plan and refuses ordinary resume,
despite retaining the final compile manifest and persistent corpus witnesses.

Implement one opt-in `--retry-graph` on the existing graph/sync commands. Limit
authorization to the saved run's selected current versions, preserve all target
locks and input/version checks, retain reports and clear blocks only on real
success. No automatic retries, migration, foreign tool patches or run-specific
code. Resume from the final manifest without repeating earlier stages.

Verification: reproduce missing flag; public CLI failure/interruption recovery,
cleaned partial recovery, unchanged default suppression, other-run/unselected
blocks, changed witnesses and target locks. Then CI/review, release, installed
co.bot UAT and the real sync in tmux. Do not run repeated full local regression.
