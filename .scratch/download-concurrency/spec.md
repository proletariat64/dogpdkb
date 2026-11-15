# Bounded download concurrency

Status: ready-for-agent

## Problem Statement

Document export waits for every Skylark process before starting the next, making network and proxy startup latency accumulate across large updates. The operator has previously tested two or three concurrent downloads and requires a hard maximum of three.

## Solution

Expose --concurrency on sync and apply. Default to two simultaneous document exports, allow one through three, and keep whole-batch stage ordering, durable results and safe interruption. Each export remains an independent public Skylark CLI request.

## User Stories

1. As an operator, I want two downloads by default so network waits overlap.
2. As an operator, I want --concurrency 3 so I can use my tested maximum.
3. As an operator, I want --concurrency 1 so I can recover serial execution.
4. As an operator, I want values outside one through three rejected before side effects.
5. As an operator, I want completed downloads checkpointed as they finish so interrupted work is reusable.
6. As an operator, I want version and identity checks preserved before success is recorded.
7. As an operator, I want individual export failures recorded while other selected documents continue.
8. As an operator, I want interruption to stop all in-flight export process groups and start no queued exports.
9. As an operator, I want resumptions to accept a different concurrency without invalidating completed inputs.
10. As an operator, I want compare to start only after the export batch has settled.
11. As an operator, I want installed English help to describe default, scope, limits and recovery.
12. As a maintainer, I want ordinary PR CI and Greptile review without local OCR review or OCR CI.

## Implementation Decisions

- --concurrency is a runtime option on sync and apply, integer choices 1, 2, 3, default 2. Invalid syntax/range uses existing argument-error behavior before creating a run, changing state or invoking tools.
- Only the document export stage becomes concurrent. Listing, comparison, OCR and native graph stages retain their current order and behavior.
- Each export receives its own existing request/report/raw destination and remains a public subprocess argv call. Do not import Skylark internals, change Skylark source, add connection pooling or modify its transport retries.
- A single coordinator owns all Store/Run changes and checkpoints. Never mutate shared durable state from worker threads. Persist each settled document's outcome while other exports may still be running, preserving same-version suppression and exact source/version checks.
- Limit active exports, not merely worker count. Start replacements as work settles, never exceed the requested number, and preserve serial semantics at one. All downloads settle before compare begins.
- Preserve signal exit conventions and process-group termination, escalation, inherited lifecycle/target lock descriptors and redirected progress logs. One main-thread signal owner must stop all in-flight exports, prevent further dispatch, and preserve truthful completed/failed/unknown state. Python signal handlers cannot be installed in worker threads.
- Resume may use a new concurrency. The value does not participate in document identity/version checks and does not trigger refetch or automatic retries. Explicit graph-only recovery does not restart downloads.
- Keep existing per-document atomic reports/checkpoints and public tool contracts. No database, migration, new fingerprint mechanism or rate-adaptive scheduler.
- Documentation/spec/code are English. Greptile is the AI reviewer; no local OCR review, OCR CI, full local make check or make smoke.

## Testing Decisions

Use existing public CLI subprocess fixtures with a local fake Skylark executable and real temporary Markdown files. Deterministic handshakes/events and in-flight markers prove overlap and upper bounds for one, two and three, without timing-only speed claims. Verify invalid options have no tool/state side effects; mixed success/failure and version drift remain truthful; settled checkpoints survive interruption; resume skips completed exports; all in-flight descendants terminate on SIGINT/SIGTERM and no queued tasks start; comparison waits for the batch. Run focused process/export/help checks locally, then ordinary full PR CI. Request Greptile review on the actual latest head and fix valid findings through latest-head 5/5.

## Out of Scope

Parallel listing/compare/OCR/Graphify, Skylark connection reuse or source changes, new download retries, raising concurrency above three, production-job interruption, release or deployment of this feature without the next operator instruction.

## Further Notes

The operator confirmed this plan before implementation. Use the established public CLI testing seam; no new user interview is needed. Preserve unrelated worktrees, production sessions and original-checkout UAT evidence.
