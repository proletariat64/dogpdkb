# Graphify native CLI integration

Graphify is an unmodified external dependency. No fork, patch, private Python
imports, custom commands or foreign merge/replacement algorithms are permitted.

## Invocation and scope

`graphify extract CORPUS --out WORKSPACE --no-cluster` scans a complete Markdown
source tree. Only `compile` manifest rows request a new graph attempt; `reuse`,
`metadata`, `retain` and `defer` do not claim extraction or advance graph facts.
An all-retained batch makes no Graphify call. A deferred row keeps the result
partial even when all selected compilation succeeds.

For a mixed batch, the orchestrator freezes a run-owned complete corpus mirror.
Selected sources contain current complete bytes. Unselected registered sources
with retained graph evidence contain their previous complete graph input bytes,
at their previous relative `source_file` location; a newly published rename is
removed from the mirror so it does not create a duplicate source. Other source
files and resources remain in the complete tree. The actual completed graph
step's input witness is preferred; otherwise `graph_baseline.path` and
`graph_baseline.source_file` provide the frozen pre-publication descriptor.
Missing required retained evidence stops the batch with
`GRAPHIFY_RETAINED_INPUT_REQUIRED` before any external call. Selected and
substituted source bytes are rechecked after extraction, before publication.
The public corpus is never rewritten to construct this view.

Graphify's own manifest and cache decide which files need extraction and perform
the native incremental merge. The orchestrator does not submit diff fragments,
select graph records or reproduce cache identities.
`update` is AST-only and is not a document compilation command.

No temporary ignore/exclude rules implement selection: native Graphify prunes
excluded graph sources. The mirror preserves the full source universe instead.
Native zero-node recovery and resource/cache decisions can still dispatch files
beyond the requested body candidates; candidate counts are not a guarantee of
model-call counts. Frozen previous bodies prevent the latest uncertain held
body from being submitted as an implicit full-document fallback. Native baseline
and ownership semantics remain authoritative; no hash or manifest is fabricated.
Mirroring requires a portable relative-key native manifest. Legacy absolute-key
manifests return `GRAPHIFY_PORTABLE_BASELINE_REQUIRED` rather than causing a broad
re-extraction after relocation. This check never rewrites native entries.

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
directory compilation when that version would be scanned. An unselected source
with a prior retained witness is checked at that witness's version instead: its
newer failed corpus version is not submitted or retried and does not block other
selected sources. An external
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
Its successful full input descriptor (body witness, relative source path,
version, metadata and available resource/OCR evidence) becomes `graph_baseline`
for subsequent comparison and mixed-batch mirrors. Retained/deferred graph
facts and their extraction witnesses stay unchanged.
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
The mixed-batch probe publishes a newer held body while retaining its previous
graph witness, then verifies the native mirror submits only the selected changed
source and preserves the held source's graph nodes. Executable-fake tests also
cover retained/deferred state, resource copying, renames and missing witnesses.
