# Graphify native CLI integration

Graphify is an unmodified external dependency. No fork, patch, private Python
imports, custom commands or foreign merge/replacement algorithms are permitted.

## Invocation and scope

`graphify extract CORPUS --out WORKSPACE --no-cluster` scans the selected book's
complete Markdown corpus. Graphify's own manifest and cache decide which files
need extraction and perform the native incremental merge. The orchestrator does
not submit diff fragments, select graph records or reproduce cache identities.
`update` is AST-only and is not a document compilation command.

Compilation is a directory-level batch, accepted by the user on 2026-11-13.
There is no invented per-source Graphify request/result protocol. The compile
manifest remains a dogpdkb input: it freezes complete source paths and observed
versions and is rechecked after the subprocess finishes.

## Baseline and suppressed work

An existing graph requires its original Graphify `manifest.json`. If missing,
stop before invoking Graphify: silently extracting historical files is forbidden.
For a new graph, unselected historical Markdown also stops the run. Establish a
baseline separately using the original CLI; init-state does not invent one.
A failed/unknown graph attempt at the current corpus version blocks subsequent
directory compilation, including batches selecting other sources. An external
operator must diagnose it or a new version must arrive. After fixing the cause,
`sync --resume RUN --retry-graph` (or `update-graph --input MANIFEST --retry-graph`)
may retry only failed/unknown/in-flight graph versions belonging to that saved
run and present in its validated final compile manifest. Target locks, source
identity, input witnesses, metadata and newer-version checks still apply. A
blocked source outside that batch or belonging to another run still stops the
directory scan. No suppression is cleared before execution; only actual success
clears the matching graph block. Each attempt uses a new workspace/report and
retains earlier failure evidence. Ordinary sync never opts into this recovery.
Separate capture/OCR failures do not block compilation of usable, published
corpus sources; they keep the overall run partial. Failed raw inputs are not
added to the compile manifest.

## Outputs and publication

Copy the existing Graphify output directory to a run-owned workspace, preserving
its manifest/cache. Invoke the original CLI with `GRAPHIFY_OUT=graphify-out`, so
custom instance graph locations still use the same native output convention.
Graphify extraction and cache writes occur only in this workspace.

Require exit zero, a readable graph JSON with public node/edge arrays, and a
manifest JSON object. Do not reject Graphify's internal duplicate/chunk semantics.
Recheck all frozen inputs, copy native auxiliary/cache artifacts, then atomically
write the complete graph artifact and its native manifest. No node, edge,
hyperedge, source identity or metadata is rewritten. The Graphify manifest is
published after the graph, so it cannot make a later scan skip an absent graph.
A failed extraction leaves public graph and manifest unchanged. Publication I/O
failure or interruption is unknown and keeps evidence for the cleanup agent.

Only the complete successful batch advances selected documents' graph facts.
A nonzero extraction fails the entire batch; there is no per-source success
claim. `dogpdkb-graph-result-1` is the orchestrator's own execution evidence, not a
schema required from Graphify. Directory scans are not remote atomic snapshots.
Execution evidence includes the actual sanitized argv and exit code. Successful
graph witnesses and latest reports live outside wash; completed/partial sync
reclaims the duplicate candidate workspace while retaining necessary failure
reports and source inputs. Manual purge never removes the published graph/cache.

## Verification

Tests cover exact native argv, baseline guards, unchanged/changed input,
whole-batch failure, interruption, suppression and publication. An isolated real
CLI probe uses a localhost OpenAI-compatible test provider: two initial sources,
one changed source, then an unchanged scan. No production graph or paid provider
is used. Installed Graphify is selected via the existing `[tools].graphify` argv;
provider settings remain its own environment/configuration.
