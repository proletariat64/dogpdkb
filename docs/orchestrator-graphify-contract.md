# Graphify native CLI integration

Graphify is an external dependency. This repository uses the separately
installed 0.9.111 CLI with the existing `openai-oauth` patch. Dogpdkb does not
patch or import Graphify, add custom commands, or implement graph merge logic.

## Invocation and scope

`sync` and `update-graph` accept `--graphify-inputs {all,text}` (default `text`).
`text` passes a run-owned mirror containing only Markdown (`.md`, case-insensitive)
and the original `.gitignore` / `.graphifyignore` scan controls. Images, PDF,
code and other non-Markdown files are not copied into that input tree. Original
corpus resources remain unchanged for OCR and publication; OCR text already in
Markdown is preserved byte for byte. `all` keeps the previous complete-tree scan,
including resources. For a newly sealed sync batch, recovery inherits the saved
mode and refuses a different one. Apply and older unbound batches retain their
per-invocation option. The execution report records `graphify_inputs`.

`graphify extract CORPUS --out WORKSPACE --no-cluster --backend openai-oauth --max-concurrency 3`
scans that source tree, with `GRAPHIFY_OPENAI_OAUTH_PARALLEL=1` and force mode
explicitly disabled. This is one directory subprocess with up to three native
semantic chunks in flight, not three subprocesses. Graphify owns its OAuth
login, token refresh and retries; dogpdkb does not use Pi or API-key fallback.
Only `compile` manifest rows request a new graph attempt; `reuse`,
`metadata`, `retain` and `defer` do not claim extraction or advance graph facts.
An all-retained batch makes no Graphify call. A deferred row keeps the result
partial even when all selected compilation succeeds.

For text mode or a mixed batch, the orchestrator freezes a run-owned corpus mirror.
The mirror is a sibling of the native output workspace, never its descendant.
Graphify 0.9.96 anchors relative manifest keys to the output parent when the
scan root is underneath it; keeping separate siblings preserves the original
corpus-relative keys across extraction and publication.
Selected sources contain current complete bytes. Unselected registered sources
with retained graph evidence contain their previous complete graph input bytes,
at their previous relative `source_file` location; a newly published rename is
removed from the mirror so it does not create a duplicate source. Other source
Markdown files remain in the tree; all mode also retains other files and resources.
The actual completed graph
step's input witness is preferred; otherwise `graph_baseline.path` and
`graph_baseline.source_file` provide the frozen pre-publication descriptor.
Missing required retained evidence stops the batch with
`GRAPHIFY_RETAINED_INPUT_REQUIRED` before any external call. Selected and
substituted source bytes are rechecked after extraction, before publication.
The public corpus is never rewritten to construct this view.
One narrow exception is a new deferred source with no registered baseline,
graph baseline or graph fact: it may be omitted from the mirror only when both
the native manifest and all public graph source ownership records show no
contribution for that path. This cannot delete existing native ownership or
submit its uncertain body. The execution report lists such
`omitted_deferred_sources`; their graph facts and failed attempts remain intact.
The orchestrator's execution report records its actual `corpus` input root.
Failed mirrors remain run-owned evidence; successful mirrors are disposable.

Graphify's own manifest and cache decide which files need extraction and perform
the native incremental merge. The orchestrator does not submit diff fragments,
select graph records or reproduce cache identities.
`update` is AST-only and is not a document compilation command.

No temporary ignore/exclude rules implement document selection. The mirror
preserves the full Markdown source universe. Text mode intentionally omits
non-Markdown sources: Graphify may natively prune their existing graph records
when the mode changes from all to text. No graph/manifest/cache records are
rewritten by the orchestrator to hide that transition, and publication still
requires native extraction success. Choose all to retain the prior source scope.
Historical Markdown not selected for compilation must already have a native
current-byte semantic stamp and graph ownership in the frozen view. Otherwise
zero-node recovery or a missing stamp could silently redispatch it. This guard
also stops unselected Markdown whose scan exclusion cannot be proven without
extracting. All mode additionally allows native resource/cache work. Candidate
counts do not bound model-call counts. Native baseline and ownership semantics
remain authoritative; no hash or manifest is fabricated.
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
replanned same-run source now marked retain/defer is also eligible for explicit
recovery when its prior input witness is validated or the new deferred source
has proven absence from native ownership as described above. This authorizes
the batch to proceed without clearing its blocked record or marking that
unselected source successfully extracted. Ordinary execution and failures owned
by another run remain suppressed.
A blocked source outside that batch or belonging to another run still stops the
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

Before submitting corpus inputs, check the pinned CLI version and use a
synthetic missing-login probe with an isolated home and no live credential.
An unknown version or absent OAuth backend keeps the graph partial without
sending corpus data. The probe checks CLI compatibility, not live auth or
proof of a particular installation's provenance.

Exit zero and valid JSON are necessary but insufficient. Require the native
write/no-change diagnostic. If native semantic work was dispatched, require
all numbered chunks to complete with one consistent total, and reject native
incomplete-extraction, manifest or cache errors. Every frozen Markdown source
requires a current native semantic stamp and graph ownership by a node or a
nonempty hyperedge with existing members. Sources without that evidence in the
previous public generation must be accounted for by native dispatch or cache
hit counts; a terminal write alone cannot prove new work. Validate other present
manifest stamps against their frozen bytes. The native manifest
uses MD5 as a change stamp; dogpdkb records SHA-256 input witnesses separately.
No size growth or shrink threshold substitutes for these checks.

Recheck the full frozen input tree, source witnesses and prior public generation.
Fsync and exchange the candidate directory with the old one on the public
filesystem (Linux `renameat2(RENAME_EXCHANGE)`). The graph, native manifest,
cache and auxiliary files switch together, with the same lock inode. No native
record is rewritten. Extraction or proof failure leaves the public generation
unchanged. Publication I/O failure or interruption is unknown and keeps
evidence; a crash after the swap but before state acknowledgment does not
constitute automatic resend permission.

Only the complete successful batch advances selected documents' graph facts.
Its successful full input descriptor (body witness, relative source path,
version, metadata and available resource/OCR evidence) becomes `graph_baseline`
for subsequent comparison and mixed-batch mirrors. Retained/deferred graph
facts and their extraction witnesses stay unchanged.
A nonzero extraction fails the entire batch; there is no per-source success
claim. `dogpdkb-graph-result-1` is the orchestrator's own execution evidence, not a
schema required from Graphify. Graph facts include SHA-256 of the actual input
bytes. An unsuccessful wrapper exit is not evidence about provider transmission;
only a proven pre-dispatch failure is `not_sent` in the handoff. Directory scans
are not remote atomic snapshots.
Execution evidence includes the actual sanitized argv, exit code and native log.
Successful graph witnesses and reports live outside wash in attempt-specific
paths; completed/partial sync reclaims the duplicate candidate workspace while
retaining necessary failure reports, logs and source inputs. Manual purge never removes the published graph/cache.

## Verification

Tests cover exact native argv, baseline guards, unchanged/changed input,
whole-batch failure, interruption, suppression and publication. An isolated real
CLI probe uses a loopback SSE fixture with synthetic Graphify OAuth credentials:
two initial sources,
one changed source, then an unchanged scan. No production graph or paid provider
is used. Installed Graphify is selected via the existing `[tools].graphify` argv;
provider settings remain its own environment/configuration.
The mixed-batch probe publishes a newer held body while retaining its previous
graph witness, then verifies the native mirror submits only the selected changed
source and preserves the held source's graph nodes. Executable-fake tests also
cover retained/deferred state, resource copying, renames and missing witnesses.
