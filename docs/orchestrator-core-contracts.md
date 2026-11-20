# Orchestrator core contract

This is the small shared API for the CLI rewrite, not a generic workflow layer.
All persisted paths are absolute. Configuration paths are relative to the selected
TOML file, book corpus paths to the books JSON. The public acceptance seam is the
installed `dogpdkb` CLI; no private injection interfaces are added.

## Settings

`dogpdkb.settings.load_config(path=None) -> Settings` reads explicit `--config`,
then `DOGPDKB_CONFIG`, then checkout `default.toml` / caller `dogpdkb.toml`.
`select_book(settings, key=None) -> Book` requires one enabled subscription
when the key is omitted.

- `Book`: `key`, `name`, `corpus`, `sources`. A source is `yuque` (URL and
  optional document IDs) or `directory` (root path). Paths resolve relative to
  the subscriptions JSON. Source keys are unique within the subscription.
  `Book.select_sources` defaults to all enabled sources and rejects unknown,
  disabled or duplicate explicit keys. Old top-level URL/scope is rejected.
- For sync and doctor, `--dir PATH` selects only a Markdown directory, excludes
  `--source`, and resolves PATH from the caller's cwd. A unique configured
  directory binding at this canonical root is reused; otherwise a new identity
  binds the canonical root to the selected book without changing subscriptions.
  Multiple matching bindings require an explicit registered source selection.
  Resume accepts neither `--source` nor `--dir`.
- `Settings`: `file`, `books_file`, `corpus`, `staging`, `state`, `web`,
  `graph_dir` (optional), `mcp_url`, `proxy_bin`, `timeout_seconds`,
  `ocr_budget_seconds`, `ocr_cache`, `results`, optional `body_recovery`.
  `[paths].body_recovery` resolves relative to the selected config and names an
  explicit public `mdcompare-body-recovery-1` manifest; it defaults to absent.
- `settings.graph_for(book)` returns configured graph directory or the book's
  `graphify-out`; `settings.command(name)` returns an argv list from `[tools]`
  (`mdcompare`, `mdocr`, `graphify`, `skylark`; strings mean an executable,
  not shell text). Missing entries default to the command name.
- Existing `[source] books_file`, `[paths]` and `[skylark]` sections remain
  convenient configuration, without importing mdocr's configuration model.

## Download scheduling

`sync` and `apply` accept the runtime option `--concurrency` with integer choices
1, 2 and 3, default 3. It is not a Settings/TOML field and does not participate in
source identity or version checks. Invalid values fail before operational writes
or subprocess dispatch. Each in-flight document retains a separate public Skylark
request/report and raw destination. The coordinator alone updates Store/Run and
persists settled results while other exports may still be active. An isolated
export failure does not stop eligible peers. The complete export batch settles
before comparison begins; later whole-batch stages are unchanged.

Resume may select a different concurrency while reusing verified completed
inputs and preserving same-version suppression. The main-thread signal owner
stops all active export process groups on SIGINT/SIGTERM, dispatches no queued
work afterward and retains truthful completed/failed/unknown outcomes. Child lock
inheritance, termination escalation and signal exit conventions remain unchanged.
Graph-only recovery does not re-enter the export scheduler.

## Durable records

`dogpdkb.records.Store(settings, book)` reads/writes the new state file at
`settings.state / book.key / state.json`. It never imports old run/state formats.
Rebinding a registered source key to a different type, URL, scope or path is
rejected. State schema `dogpdkb-state-2` has `book`, `observation` and
`documents`; observations are keyed by source. Document keys combine source
key and underlying ID, preserving the components in record fields. Product
stages own factual changes and persist them via `store.save()`.

- `store.document(id)` returns/creates that source's dict, initially `steps={}`.
- `store.step(id, name)` returns the latest public step record or None.
- `store.attempt(id, name, version, run_id, **details)` persists `running` before
  an external call; `store.complete(id, name, version, status, run_id, **details)`
  persists the actual result. Statuses are `completed`, `failed`, `unknown`,
  `skipped`; step details may hold artifact/reason/exit_code/input references.
- `store.complete_batch(records)` uses the same step fields and version-block
  rules as `complete`, but persists all records with one atomic state write.
  Each record supplies `doc_id`, `name`, `version`, `status`, `run_id` and any
  step details. Comparison and Graphify mark their complete external batch
  `running` before the first subprocess and commit outcomes once afterward.
  Comparison resource updates use one additional write. Local descriptor
  preparation does not mark an external attempt. Empty batches do not write.
  Individual export/OCR calls retain their own before/after checkpoints;
  successful export results and raw facts share one post-call write.
- `store.suppressed(id, name, version)` is true for this version's
  failed/unknown/in-flight attempt, so a later daily batch does not retry it.
  Unstarted work has no record and is not suppressed. A new version is not
  suppressed. `version_key(version)` uses an existing normalized `utc`/`value`
  timestamp or a version string; it does not hash content.
  Minimal `blocked_versions[step][version]` status/run facts preserve earlier
  failed/unknown attempts when a newer version replaces the latest step record;
  actual success clears only its own version's block.
- `store.lock()` acquires corpus and actual graph target locks together, in
  stable order and without waiting, then reloads the latest durable state.
  No recorded run prevents acquisition after a process exits. Read-only status
  does not lock. Public tool subprocesses retain both active lock descriptors.
- `atomic_json(path, payload)` and `read_json(path)` are small shared public I/O.

Document stage owners should keep `observed`, `raw`, `corpus`, `graph` facts
separate, each with its version and actual artifact path. The core imposes no
foreign tool schema. Keeping the durable state outside run directories means
deleting an old run cannot reset a failed version's suppression.

`initialize(settings, book)` registers existing `title-YYYY-MM-DD-[ID].md`
files without modifying Markdown or graph files. Each gets
`baseline={path, filename_date, precision:'day'}` and
`corpus={path, version:{value:date, precision:'day'}}`; `graph={status:'unknown'}`
does not invent a graph/raw success version. Existing document records remain.
Each legacy numeric filename must have exactly one eligible Yuque source to be
registered. Ambiguous ownership or duplicate IDs fail before registration.

## Run record

`Run.create(store, command, *, sources=None) -> Run` creates a new `run-*`
directory under `settings.staging / book.key`, regardless of old interrupted
records. Sync supplies the selected sources; ad hoc bindings are registered
under the write lock without recording observations or acquired inputs.
`Run.load(store, reference)` accepts a run ID, run directory or run JSON path.

- `run.directory`, `run.path`, `run.id`, `run.data`; `run.save()`.
- `run.stage(name, **counts)` records current stage and publicly known counters.
  Optional `phase` is a separate status field: comparison and Graphify expose
  `prepare`, `tools`, and `record-results`. These describe wrapper activity,
  not per-document comparison or extraction completion. Entering a new stage
  clears the previous phase unless the caller supplies one.
- `run.failure(..., persist=False)` accumulates a known batch's failures for
  the owner's following save/stage/finish. Normal individual failures persist
  immediately. Interrupted running steps are marked unknown in one state write,
  then the complete failure list and interrupted run result are saved together.
- `run.failure(**details)` appends a failure/evidence row.
- `run.finish(status, exit_code=0, reason=None)` records paused/completed/partial/
  failed/interrupted; it does not itself clean up or retry. The sync owner cleans
  its current completed/partial run while holding the write locks, keeping failed
  Markdown/images/reports and a compact observable record. Paused/failed/interrupted
  runs are retained. Necessary latest inputs live outside wash; no historical migration.
- `run.data['artifacts']` maps public artifact names to paths. Other product
  metadata such as selected inputs, stop-after and plan paths stays in this record.

Run JSON is schema `dogpdkb-run-2`, containing run_id, command, book, pid, status,
stage, counts, started_at, updated_at, ended_at, exit_code, reason, artifacts and
failures. Status is observationally interrupted when `running` has no live owner.
`targets` freezes source bindings and absolute corpus/graph targets. A new sync
freezes only selected sources and checks them against durable bindings. Loading
a run for execution rejects changed targets before any tool call or publication;
use a newly generated plan for a different source or target.

### Sync phase handoffs

`sync --stop-after {collect,plan,ocr,corpus,graph}` runs through the named
boundary. The first four stop with `paused`/exit 0 and retain their wash;
`graph` uses the ordinary final result and cleanup. `sync --resume RUN`
continues the same run and cannot take a new source or directory. A repeated
stop at the same phase does not re-enter the phase. The download concurrency
option remains a scheduler limit rather than part of the frozen plan.

`run.json` records `completed_phase`, SHA-256 `checkpoints` keyed by phase and
artifact, and `sync_policy`. The policy captures the selected tools, OCR and
score limits, explicit body-recovery inputs, and Graphify input mode before
collection. It does not include provider credentials. The handoffs are:

| Phase | Frozen output |
| --- | --- |
| collect | Complete `differences` scope and `prepared_inputs` (only verified acquired inputs), with separate export failures |
| plan | `plan` and frozen per-document routing; no OCR or public writes |
| ocr | Native OCR request/plan/result/journal and a `publication` handoff of verified OCR or readable fallback bytes; no corpus replacement |
| corpus | Final `manifest`, containing only published documents, and the pre-extraction public graph generation |
| graph | Settled graph exit; native result proof and atomic publication remain the Graphify adapter's responsibility |

Compare, OCR and corpus publication exclude documents whose frozen state has
been superseded (`STALE_DOCUMENT`), while nonconflicting peers continue. A
paused corpus handoff whose published inputs or previous graph generation have
changed reports `STALE_GRAPH_HANDOFF` without dispatching a new directory
extraction. If graph state already records this batch's exact successful inputs,
reconciliation does not re-extract. Ordinary resume does not change the scope of
an already admitted native OCR batch; if its scope went stale, settled children
can be verified, but the batch cannot admit further work as a whole.
Explicit graph-only retry still uses the existing Graphify recovery contract.

Corpus publication saves `publication.json` (`dogpdkb-publication-1`) before
replacing any corpus file. It freezes the complete compile rows, retained output
witnesses and optional `before_copy` snapshots; book/run identity and plan
IDs/versions/targets must match. Resume accepts only the frozen previous or
intended output bytes, preserving raw-input checks, target locks, symlink guards
and newer-version rejection. It resumes publication directly without re-entering
listing, download, comparison or OCR. Short same-directory temporary names avoid
lengthening valid final filenames; corpus facts are saved once per batch (also
on a caught partial publication error). A replacement that precedes the state
save is reconciled from the handoff. The final `dogpdkb-compile-1` manifest is
written only after publication settles and excludes `before_copy` and its hash.

A later run may select a same-version document whose OCR was provably not sent.
Before admission it revalidates the settled checkpoint, the request/result
witnesses, and the document/resource bytes. It does not resubmit verified or
failed/unknown peers. If no OCR enrichment was
added, it keeps the previous readable corpus bytes and witness. New verified
OCR bytes override authored-body reuse for that row (`OCR_ENRICHMENT_ADDED`),
so the regular graph stage picks them up in one directory batch.

A saved final compile manifest is reused without renewing its bounded OCR budget.
OCR is advisory: new manifests emit no required pending OCR steps. Historical
pending fields/failures remain readable; usable bodies proceed to Graphify.
Explicit recovery of older failed runs belongs to the external agent; there is
no automatic historical migration or retry layer.

Cleaned completed/partial runs are not ordinarily resumable. Explicit
`sync --resume RUN --retry-graph` uses only a retained final compile manifest,
including after cleanup, and revalidates it through the graph stage. It never
re-enters discovery, download, comparison, OCR or corpus publication. New sync
runs bind the manifest SHA-256 and input mode at the corpus checkpoint, and the
actual mirror tree digest before extraction. Recovery rejects changed witnesses,
mode, input tree or a superseding graph generation. If a new run never froze
its mirror, there is no graph batch to retry; resume the unfinished phase or
make a new plan. `--replan` is the separate,
explicit reselection of the same published inputs, not a way to add documents.
Historical failures stay recorded; unresolved non-graph work still yields exit 2
when graph recovery succeeds. Preexisting manifests without a binding keep the
legacy validation path; no missing witnesses are invented.
Recovery snapshots the already-cleaned run's retained file paths before the new
attempt, so cleanup preserves its failure bundles even when the old plan was
pruned. Newly created disposable graph workspace files can still be reclaimed.
Manual `purge` deletes only
selected wash; force permits discarding unfinished runs but never bypasses locks
or clears durable facts, corpus, graphs, persistent inputs or tool caches.
Purge validates the saved schema, book key, source bindings and run ID against
the selected wash; it does not require the saved corpus/graph execution targets to match the
current configuration. Protected-path and live-owner checks still apply.
Saved corpus/graph targets must be absolute and cannot overlap selected wash.
Purge acquires their locks in addition to current target locks before deleting
any candidate, retaining every acquired lock until all deletions finish. This
protects external children that retain a lock after the recorded parent exits.
Missing historical target directories are not recreated; existing directories
may receive a missing lock file without rewriting existing lock contents.

## Processes and observation

`dogpdkb.processes.run_command(argv, *, log=None, cwd=None, timeout=None) -> int`
starts a public executable without shell evaluation, streams stdout/stderr to
stderr and an optional log file, forwards termination to its process group, and
returns its exit code. Callers interpret the tool's documented artifacts/codes.
It never parses tool log text into business state. `diagnostic_argv(argv)` masks
credential arguments for durable command records.

Optional `env={...}` overrides selected variables while retaining the inherited
tool environment; it is never persisted or printed. `settings.skylark_environment()`
supplies configured gateway/proxy/timeout variables for listing and export.

`dogpdkb.observe.main(argv=None)` implements `dogpdkb status`: config/book/run,
json/watch. `view_run(path)` derives process/lock liveness and elapsed seconds.
Watch samples the same atomically saved run and book state each second (JSONL
with `--json`). The two records are independently atomic, not a cross-file
transaction. No business tool, provider, target write lock or state write is
used. Ctrl-C stops only the observer. `status` exits 0 for a readable snapshot
(including no runs and failed runs), not the observed run's exit code.

Status retains `dogpdkb-status-1` and adds:

- `progress`: phase, last completed phase, stage `total`, `completed`,
  `in_flight`, `pending`, `failed`, and last progress time. Units depend on the
  stage. Export in-flight is the scheduler's recorded admissions, not a remote
  request counter. Opaque tools may leave counts null; an upper limit is not a
  measurement. Watch does not interpret tool logs as live progress.
- `publication`: corpus and graph outcomes for this run. `retained` means this
  attempt did not replace the graph; `unknown` includes an interrupted or
  unconfirmed publication boundary. `next_action` explains how to proceed.
- `facts`: current recorded document facts for this run's selected sources,
  not a historical snapshot of the selected run. Observed version, acquired raw,
  published corpus, and graph input are separate. `sha256` is a saved byte
  witness, not a live checksum; old facts can have no digest. `corpus_vs_graph`
  compares recorded digests regardless of source-version equality.
- `handoff` (`dogpdkb-handoff-1`) is saved in terminal sync `run.json` before
  automatic wash cleanup, including a requested pause. It lists phase, run,
  source, document, version, input digest, send state, failure and evidence
  paths when known. Batch-level errors may not identify a document. Missing or
  interrupted results do not attest transmission; abrupt owner loss can show
  a provisional read-only interrupted view. Manual `purge` removes that run's
  handoff and wash evidence but not durable version suppression or native OCR
  artifacts under state.

Handoff `retry_scope` is the recorded upper bound: `none`,
`document_next_batch` (verified unsent OCR only), or `graph_batch` (the bound
manifest and its input mode, never a document-only Graphify extraction).
`status` adds current `eligibility`: `revalidate`, `stale`, `superseded` or
`none`. It is advisory, not authorizing a call; writers check the current
witnesses before dispatch. A retry never applies one version's evidence to
another. Unknown export/OCR work remains suppressed; no tool fallback or
force-send command is implied.

## Automatic body routing and explicit reselection

The public `mdcompare semantic` report is authoritative: NEW/CHANGED/UNCERTAIN
produce compile; proven EQUIVALENT produces reuse/metadata; UNAVAILABLE produces
defer and an unknown comparison record. Incomplete/ambiguous alignment of usable
bodies is UNCERTAIN, not failed extraction. Authored deletions compile. Missing or
malformed semantic reports/diff evidence stop explicitly; diagnostic catalog,
knowledge and strict compare uncertainty cannot override a valid body route.
Image/OCR changes and enrichment results never promote equality to compilation;
equivalent documents start no new OCR. Enrichment failures remain reported and
suppressed, not required pending work. Legacy retain actions remain readable in
saved artifacts; newly generated plans do not produce HOLD/retain advice.
An opt-in positive semantic-score request budget permits a separate validated
score route to override only complete `CHANGED → COMPILE` rows: 1–3 uses
retained corpus or metadata-only publication; 4–5 and unscored changes compile.
Ambiguity is advisory. Default scoring budget is zero.


A durable `graph_baseline` descriptor preserves the actual successful full body
outside wash, with path/source_file/version; future comparisons prefer the latest
successful graph witness, then that preserved basis. Only explicit init-state
corpus facts bootstrap an existing baseline. A never-compiled new document stays
NEW/compile despite captured raw or published corpus. Skip/failure/defer never
advance successful graph-baseline truth or clear required pending graph work.

Semantic reports, diff indexes and complete per-document sidecars live under
`state/<book>/semantic/<run>/comparison-*`, outside wash. Plan and compare-step
records reference these artifacts; source descriptor provenance.version records
the old and incoming versions. All expected identities, legal status/action/body
availability, source bindings and sidecar/reference completeness are validated.
A launched comparison batch with unusable authoritative evidence becomes unknown,
not permanently running and not silently retried.

Optional explicit repair manifests/source copies are frozen under that run's
semantic/recovery directory with byte witnesses. A plan references the frozen
manifest; resume validates it and its dependencies, never live config changes.
Missing repair sources defer their affected document through mdcompare. Changed
frozen inputs require a new plan. These are operator-established insertion-only
scope repairs, not inferred equivalence or an automatic historical migration.

`sync --resume RUN --retry-graph --replan` explicitly recomputes a saved published
batch's selection using its original plan and retained input/baseline snapshots.
It validates the published manifest before/after public comparison, preserves
original artifacts in a separate replan workspace and changes only manifest
actions/reasons. No export/OCR/publication repeats, elapsed OCR budget resets,
or automatic historical retry occurs. Missing original snapshots or mismatched
identities, versions, targets or published bytes stop before graph execution.

After successful corpus publication, a complete retain/reuse/metadata decision
acknowledges only a matching pending version and metadata with no pending steps.
Explicit reselection of an already published batch uses the same rule. This
prevents unchanged daily listings from repeatedly comparing handled documents;
it neither advances graph extraction facts nor discards the previous graph
baseline. Deferred comparison failures remain unknown and suppressed.

Cleanup retains the active compile manifest and its run-owned replan comparison
workspace independently of any earlier cleanup marker. An interrupted explicit
replan followed by an ordinary graph-only retry therefore keeps the revised
selection, reports and original body/baseline copies available for recovery.
