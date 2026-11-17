# Authored-text semantic routing and durable diffs

Status: implemented; full offline filtering passed; awaiting user acceptance

## Problem Statement

Unattended knowledge updates currently confuse comparison uncertainty and advisory image/OCR evidence with unavailable authored content. Old-only authored deletions can be retained indefinitely, valid new documents can depend on unrelated baselines, and compiler candidates do not carry a complete durable semantic diff. This delays knowledge publication without proportional safety or business value. Historical generated-region repairs exist only as isolated evidence, not a public tool contract.

## Solution

Provide an offline authored-text semantic comparison contract with durable complete differences. Route new documents, meaningful changes and uncertain equivalence to whole-document compilation. Skip only demonstrated equivalence. Defer only unavailable or unreliable authored-body extraction. Treat images and OCR as advisory. Explicitly adopt the already approved marker repairs without modifying source bytes or successful graph witnesses. Preserve the current Graphify Markdown input semantics in this release. Verify the entire isolated incremental population and obtain user approval of filter results before real compilation.

## User Stories

1. As an operator, I want every valid new text document selected for compilation regardless of duplication elsewhere.
2. As an operator, I want an authored deletion selected just like an addition so stale knowledge is removed by the compiler.
3. As an operator, I want uncertain equivalence to compile rather than wait for human review.
4. As an operator, I want image replacement, failed OCR and differing OCR not to decide authored-body routing.
5. As an operator, I want unavailable authored bodies deferred individually without blocking healthy documents.
6. As an operator, I want approved historical marker repairs explicitly supplied and validated, without mutating original captures.
7. As an operator, I want the remaining 26 unresolved historical repairs to stay deferred without further rescue calls.
8. As an operator, I want marker repair provenance and original source coordinates preserved.
9. As an operator, I want export metadata and proven presentation-only changes excluded without losing numbers, conditions, owners, tables or code.
10. As an operator, I want title/context changes and semantically significant movement compiled.
11. As an operator, I want a complete machine-readable diff even when the human report truncates excerpts.
12. As a future compiler author, I want old/new evidence, locations, context and add/delete/modify/move operations independent of Graphify.
13. As an operator, I want model/network configuration to be unnecessary for filtering.
14. As an operator, I want successful graph inputs, corpus publication, raw capture and observation versions kept separate.
15. As an operator, I want skipped, deferred and failed attempts never to advance a successful graph baseline.
16. As an operator, I want current CLI help to describe new commands, schemas, routing and recovery limits offline.
17. As an operator, I want a full isolated replay that accounts for all planned new/updated documents rather than another compile-only sample.
18. As an operator, I want a reviewed release only after filter acceptance, bounded real compile acceptance and the required Greptile review gate.

## Implementation Decisions

- Extend the independent mdcompare public CLI with `semantic --baseline OLD --incoming NEW --output REPORT --diff-output DIFF [--recovery MANIFEST]`. Reuse the existing descriptor manifest and body extraction/alignment implementation inside the tool. Keep legacy compare and knowledge contracts usable.
- The report schema is `semantic-1.0`, scope `authored_body`; per-document `semantic_status` is NEW, EQUIVALENT, CHANGED, UNCERTAIN or UNAVAILABLE, with action COMPILE, SKIP or DEFER. Include extraction availability, completeness, reasons and advisory status. Empty authored input is explicit NO_TEXT evidence, not fabricated compilation success.
- Stable source inputs and usable authored-body extraction are required. Missing/unreadable/invalidly scoped bodies defer. An alignment work limit or ambiguity with both bodies available is UNCERTAIN and compiles. A bad baseline elsewhere never changes a valid new document into DEFER.
- Demonstrated equality is paired-document contextual normalized equality, not cross-document reuse, fuzzy similarity or an equal summary. New documents compile even if identical to another document. Authored deletions, subject/context changes, changed numeric/conditional/table/code content and loss of previously authored content compile.
- Durable diff schema is `mdcompare-semantic-diff-1`. Preserve complete projected old/new body evidence, source input identity/path, source locations, heading context, operation and basis. Evidence may be inline or referenced by durable artifacts but never silently truncated. Uncertain alignment retains both full bodies rather than pretending to have exact edits. Input drift prevents stale equality claims.
- Approved recovery input schema is `mdcompare-body-recovery-1`, with document identity, side (baseline/incoming), immutable source-copy path and ordered original UTF-8 byte-offset marker insertions. Validate exact equality to the declared immutable source copy, byte offsets, insertion-only marker/newline syntax and replay before projecting. No arbitrary content replacement, URL/byte identity requirement or inference that every old-only line is OCR. Expose applied repair provenance and map repaired coordinates back to original source positions. Invalid repair evidence defers its document; invalid whole-manifest schema is a CLI error.
- Recovery is an explicit user-owned artifact, not knowledge of experiment directory layout. Tool-owned projection can consume repairs; orchestrator passes only public CLI arguments/artifacts. The approved 708 witnesses are converted once into this schema for isolated acceptance. No salvage algorithm or model work for the remaining 26.
- Orchestrator runs the semantic CLI as the authoritative body route and retains strict compare/knowledge reports as diagnostics where required. All missing/stale/malformed authoritative output remains an explicit error/defer, not invented successful comparison. Once the semantic report is valid, OCR/resource uncertainty cannot override its route. OCR enrichment is optional work and cannot promote an equivalent document to compile.
- An optional instance path to the explicit recovery manifest is configured through public settings, resolved relative to the selected config. Snapshot repair dependencies for durable plan/resume evidence; later changes cannot silently alter an already frozen plan.
- Graphify keeps complete current Markdown inputs and prior retained graph witnesses under the existing public contract. Do not implement author-only graph projection, diff-based graph merging, native manifest edits or provider changes here.
- Preserve existing separation of observed/raw/corpus/graph facts, input witnesses, successful graph baselines, version suppression, locks and publication checks. This feature adds no automatic retries or old-run migration.
- Filtering defaults to deterministic offline execution. No implicit external classifier, DeepSeek key, per-document model calls or OCR work is authorized by this implementation. A future optional model adapter must have its own explicit budget and cannot be necessary for progress.

## Testing Decisions

The accepted public seams are the installed mdcompare CLI with JSON artifacts and the installed dogpdkb CLI with plan/manifest/state observations. These are the existing product acceptance interfaces approved in the conversation, not new private test hooks. Use vertical red/green slices with real Markdown and controlled public subprocess fakes.

Cover new documents with unrelated bad baselines; insertion/deletion; numeric, condition, owner, heading and table/code changes; demonstrated formatting equivalence; context moves; complete evidence beyond excerpt limits; budget-exceeded but usable bodies; invalid inputs; explicit repair replay, drift, marker-only validation and original-coordinate traceability; advisory OCR/resource changes; no OCR-driven compile promotion; resume, cleanup and graph witness invariants. Verify normal/full help offline and packaged smoke. Do not run paid OCR or Graphify during these gates.

Isolated acceptance uses the existing UAT snapshots without rewriting them: inventory the complete original increment population, replay all planned new and updated documents, consume the 708 approved repairs, preserve the final 26 failures, and account for every source in the downstream candidate/skip/defer sets. Do not backfill intentionally filtered historical documents. Preserve artifacts for user inspection. Only after user approval run a 20-plus-document real Graphify/OpenAI-OAuth smoke with an agreed token/cost cap.

## Out of Scope

Further rescue of 26 unresolved cases; treating repaired as automatically equivalent; author-only Graphify projection; automatic historical backfill; graph algorithm/provider forks; LLM dependency or new paid experiments; automatic migration/retry loops; rewriting source/UAT/archives; web publication; merging/releasing before acceptance gates.

## Further Notes

The user explicitly requests a new worktree/branch, to-spec then implement-spec, smoke, PR, comment `@greptileai review pr`, validation/fixes through Greptile confidence 5/5, another smoke, then merge and release. Filter acceptance is an explicit user checkpoint before real compilation. A hosted reviewer score cannot be manufactured; if the reviewer cannot produce the required result, report the blocker.

The authoritative repair handoff is `/tmp/dogpdkb-defer-handoff.md`; witness index is `/tmp/dogpdkb-defer-boundary-repair-v8/combined-index.json`. These machine-local artifacts must not enter Git. This spec supersedes the previous unattended-body policy that retained complete uncertain/deletion-only advice.
