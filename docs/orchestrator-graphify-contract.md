# Graphify native CLI integration

Graphify is an external dependency. Dogpdkb invokes the separately installed
native CLI through its configured command; it does not import Graphify, patch it,
or reproduce its algorithms. Graphify owns scanning, exclusions, incremental
cache, merging, output and repair.

## Fixed native handoff

For each book, `sync` and `update-graph` call `graphify extract` once on the
book's fixed `corpus` directory and target its fixed configured graph directory
(default `corpus/graphify-out`). The directory scan is native Graphify's scope:
plan selection chooses Corpus documents to publish, but is not a per-document
Graphify inclusion/exclusion list. Do not create a mirrored input tree, copy or
exchange graph output, provide per-run scan modes, or require an operator scope
file, checksum, or other wrapper-generated proof. Existing corpus files and
native Graphify output are used in place.

Graphify's manifest and cache decide which sources need work. The orchestrator
uses its workflow state to suppress a new directory dispatch when it would
silently resend a known failed or unknown input at the same version. This does
not inspect graph internals or query native cache to construct a historical hold
inventory. When there is no pending compilation work (including an unchanged
run), Graphify is not invoked. Graphify and the orchestrator retain their writer
locks; Corpus publication remains ordered before graph dispatch.

The configured Graphify CLI and its own authentication/runtime remain external
prerequisites. The orchestrator does not add Pi, API-key fallback, or provider
routing. Configuration may pass Graphify's supported model, concurrency and
token-budget options. These are execution settings, not document-scope controls.
Normal resume uses the saved run's settings. `--retry-graph` explicitly retries
graph work for that same run after revalidating its saved batch and current
inputs; it is not an automatic retry or a way for a later run to resend a known
failed/unknown version. Existing ordinary run resume, same-run retry and writer
locks remain supported.

## Results and failure behavior

Only compile work dispatches Graphify. Reuse/retain/defer work does not itself
request extraction; deferred or failed work can leave the overall run partial.
Dogpdkb records the native command's exit/status evidence and advances selected
graph facts only for a complete native success. If Graphify's documented result
reports partial work, even with exit zero, dogpdkb does not acknowledge the
batch as complete. Success and partial evidence come from the native result and
run records; dogpdkb does not inspect internal graph contents to infer coverage,
provenance, cache validity, or semantic quality.

A Graphify failure or interruption can occur after native output files have
changed. Dogpdkb does not promise rollback or that old native files remain
unchanged. It preserves already-published Corpus files, does not acknowledge a
new graph version for an incomplete result, and reports failure versus unknown
interrupted outcome where known. Inspect the recorded run result and native log
for diagnosis. A subsequent scheduled run does not silently retry an attempt
suppressed by same-version failure/unknown state; repair the cause in Graphify
or explicitly retry the original run when eligible.

There is no historical compatibility, migration, import, replay, or repair
contract for prior wrapper-generated runs, mirrors, candidate outputs, or
receipts. Ordinary runs created by the direct-native workflow can resume and
explicitly retry their own graph work as described above. The one-off
`recover-ocr` command is not part of the supported CLI; OCR's normal request
handling and ordinary sync resume remain available.

## Verification boundary

Tests and CLI checks should verify the fixed Corpus and native output paths,
that unchanged sync avoids Graphify, that the writer locks and normal OCR resume
remain in force, and that fresh failure/interruption runs preserve Corpus without
advancing graph facts or causing a later same-version resend. Same-run explicit
retry should revalidate and operate on the original run. Private UAT evidence may
observe native manifest paths, but such observations are not a production gate
or a reason for dogpdkb to inspect Graphify's internal graph.
