# Direct native Graphify for scheduled sync

Status: ready-for-agent

## Problem Statement

The scheduled plan and sync tools wrap native Graphify in a temporary input tree, copied output, candidate checks and directory exchange. The wrapper obscures what is scanned, duplicates disk work, and led to one-off repair commands and operator-supplied scope checks. The rebuild workspace requires one fixed Corpus and one fixed native output per book.

## Solution

Correct the integration by removing the wrapper, not by adding features. Rebuild each knowledge base with one fixed Corpus and one fixed native Graphify output directory. The scheduled job delivers final Corpus files, calls Graphify under the writer locks when there is work, and records the native result. Operators use ordinary plan, sync and resume; no manual scope file or checksum is required.

## User Stories

1. As a cron operator, I run ordinary `sync` without making approval files, hashes, mirrors or per-run Graphify directories.
2. As a book owner, I select which documents to ingest through the normal plan. That selection is not an exclusion list for Graphify's fixed directory scan.
3. As an operator, I can see Graphify failed or was interrupted without losing the already-published Corpus or acknowledging a new graph version. Native output may have changed; dogpdkb makes no rollback promise.
4. As an operator, I can resume a run created by the rebuilt workflow without repeating settled stages, and explicitly retry that same run's graph work if its inputs are still current. A later scheduled run does not silently resend a failed or unknown attempt at the same version.
5. As a maintainer, I fix Graphify extraction and incremental behavior in Graphify, and leave mdocr's durable ordinary resume and request handling in place. A one-off historical OCR recovery procedure is not a supported dogpdkb command.
6. As an operator, I can inspect the native log and recorded result by run ID. No terminal evidence is invented from a process exit or unrelated old graph file.
7. As a cron operator, an unchanged run does not start OCR or Graphify, even if those tools could serve the same inputs from cache.
8. As a maintainer, I can reproduce the handoff in a private workspace without changing production or resending known OCR requests.

## Implementation Decisions

- Retain the existing state and graph writer locks and ordered Corpus publication. Invoke native `extract` once on the book's fixed Corpus, targeting the configured graph directory (default `Corpus/graphify-out`). Graphify owns scan exclusions, merge, incremental cache, output and repair. No wrapper tree copies, renamed retained inputs, candidate path, output exchange, graph-internal coverage inference, or per-run text/all scan mode.
- Use current workflow state to prevent silent same-version resends. If the fixed scan could resend a known failed or unknown input without explicit authorization for that same run, skip graph dispatch and report the block. New Corpus files may still be published. Without pending compile work, do not invoke Graphify. Do not build a historical hold inventory or query the native cache to make this decision.
- Advance selected graph facts only after a complete native result. If the documented CLI result reports partial work even with exit zero, do not acknowledge it. Distinguish failure from interruption with unknown outcome; do not claim the old files are unchanged. No independent graph coverage, provenance validation, or cache verification. Keep normal resume and explicit same-run retry for the rebuilt workflow; do not add fallback or automatic retry paths.
- Remove the one-off `dogpdkb recover-ocr` command and code used only for that salvage path. Preserve ordinary sealed sync resume, mdocr request handling, and its root-cause fixes. Standalone assembly and staged export admission are outside this change, not compatibility requirements.
- Do not add a permanent feature unless used repeatedly in normal operation and still needed after fixing the root cause. Use existing contracts or direct comparisons, not new guards or fingerprints. The removed outer output workflow and OCR salvage command fail this test.

## Testing Decisions

- At the CLI boundary, verify fixed input and output paths, no extra graph copy, no dispatch on unchanged sync, locks, and ordinary OCR resume. Create failure and interruption in fresh runs: Corpus survives without graph acknowledgement, another run does not resend the held version, and authorized same-run retry works.
- Start the private `/tmp` UAT for the agreed ten real documents with empty run state and native output. Exercise real collection and fixed-directory native Graphify. Use the three finalized OCR bodies as test fixtures, not as evidence of new OCR or a reason to add an importer. Observe ten paths in the native manifest as UAT evidence only, never a production gate. Verify an unchanged run starts neither OCR nor Graphify. Report which stages ran; no production writes or Batch 2.

## Out of Scope

Legacy data, state, runs, and graph output are left untouched. No legacy compatibility requirement, migration, import, replay, or repair. Production release, installation and cutover; Batch 1 or 2 dispatch; snapshot or backup features; provider or route changes; replacing Graphify or mdocr algorithms.
