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

Every extract passes `--no-gitignore`, so `.gitignore`, `.git/info/exclude` and
Git's user-wide excludes cannot remove published Corpus sources from the scan.
Graphify's own `.graphifyignore` rules remain active. Before dispatch inside a
Git worktree with active local Git ignore rules, dogpdkb selects the nearest
existing `.graphifyignore` in the worktree-to-Corpus directory chain, defaulting
to the worktree root. Existing complete whitelists require no write. An existing
incomplete whitelist remains byte-identical and produces an operator-provisioning
warning; dogpdkb never replaces an existing policy file. This preserves concurrent
operator edits, ownership, ACLs, symlinks, and hard links.

A previously absent destination is written to a same-directory temporary file,
fsynced, and published with atomic no-overwrite link creation. Concurrent creators
preserve the winning destination and warn. Disappearing files, permission
failures, unavailable Git exclude lookup, resource exhaustion, and unsupported or
read-only locations preserve the original and emit a warning while
`--no-gitignore` keeps extraction usable.

Linked worktrees resolve `info/exclude` through `git rev-parse --git-path`, with
relative filesystem-decoded output resolved against the selected worktree. Git
probes handle BOMs and first-column comments, skip non-regular or symlinked
sources, preserve non-UTF-8 bytes, and clear repository-selection environment
overrides. Non-regular destinations and Corpus paths containing pattern syntax or
line breaks are rejected before modification. This small scope guard is
orchestrator-owned input selection; Graphify still owns pattern evaluation and
scanning.

Graphify's manifest and cache decide which sources need work. The orchestrator
uses its workflow state to suppress a new directory dispatch when it would
silently resend a known failed or unknown input at the same version. This does
not inspect graph internals or query native cache to construct a historical hold
inventory. When there is no pending compilation work (including an unchanged
run), Graphify is not invoked. Before dispatch, dogpdkb records the directory attempt
and the unacknowledged current Corpus it could send, including documents
outside this compile plan. Only selected documents receive new graph facts.
The writer locks remain in force; Corpus publication precedes graph dispatch.

The configured Graphify CLI and its own authentication/runtime remain external
prerequisites. The orchestrator does not add Pi, API-key fallback, or provider
routing. Configuration may pass Graphify's supported model, concurrency and
token-budget options. These are execution settings, not document-scope controls.
Normal resume uses the saved run's settings. `--retry-graph` explicitly retries
graph work for that same run after revalidating its saved batch and current
inputs. An interrupted run may have written native output; retry recognizes
that run’s last recorded dispatch without treating its own writes as a newer
run’s result. This is not automatic retry or permission for a later run to
resend a known failed/unknown version. Existing ordinary run resume, same-run retry and writer
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
