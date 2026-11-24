# Checkpoint image staging and resume before freeze

Status: ready-for-agent
Type: task
Blocked by: [02](02-accept-draft.md)

Implement the download-checkpoint and pre-freeze resume sections of
[the specification](../spec.md). Reuse `images/`, the existing plan
normalization, path checks, and atomic file operations. Read the effective
policy from the checkpoint or frozen plan when resuming. For a frozen image
repair, require the exact frozen digest before replacing owned staged bytes.
Keep cancellation scoped to accepted plans and keep `assemble` offline.

Prove the public CLI can resume from a checkpoint after interrupted staging.
Check provider settings and source binding, committed and unrecorded files,
missing or damaged bytes, and changed content after freeze.
