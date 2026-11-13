# feat: Independent Skylark help and durable delivery evidence

Type: task
Status: resolved
Blocked by: none

## Scope

Implement the spec in independent restored Skylark source/build, preserving original checkout dirty changes. Add correct command help, full local contracts, short help, root version, usage=64 and subprocess tests. Deliver a reviewable patch and build/install evidence in this effort; do not vendor its runtime into dogpdkb.

Spec: [CLI help redesign](../spec.md).

## Acceptance

Meet the relevant spec decisions through the agreed public CLI seam. Record red/green evidence, focused checks, delivered commit or patch, and limitations before resolving.

## Comments

## Answer

Independent durable source commit 0dbb2cc on feat/cli-local-help, exact restored parent 0e7c218. Build, transport/CLI tests and fresh offline package install passed; see ../skylark-acceptance.md.
