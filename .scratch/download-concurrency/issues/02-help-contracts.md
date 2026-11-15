# 02: Installed help and integration documentation

Status: resolved
Type: task

Document the approved --concurrency runtime contract in installed sync/apply help, README and relevant orchestration contracts. Own documentation/help resources only; runtime parsers and tests belong to 01. Keep guidance English and avoid claims of measured speedup.

## Acceptance

State default two, allowed one through three, download-only scope, batch ordering, resume behavior and interrupt semantics. No new configuration setting or Skylark flag.

## Answer

Installed sync/apply full help, README examples and core/Skylark/unattended contracts describe --concurrency, default two, allowed one through three, per-invocation download-only scope, checkpoint ownership, independent failure handling, resume adjustment and process-group interruption. No new TOML field or Skylark argument is introduced. Serial stage wording now distinguishes whole-batch sequencing from concurrent exports.

Validation: doc guard passed for 156 documents; git diff --check passed. No runtime changes, tests, OCR, external services or full regression were run for this documentation slice.
