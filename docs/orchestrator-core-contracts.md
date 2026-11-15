# Orchestrator core contract

This is the small shared API for the CLI rewrite, not a generic workflow layer.
All persisted paths are absolute. Configuration paths are relative to the selected
TOML file, book corpus paths to the books JSON. The public acceptance seam is the
installed `dogpdkb` CLI; no private injection interfaces are added.

## Settings

`dogpdkb.settings.load_config(path=None) -> Settings` reads explicit `--config`,
then `DOGPDKB_CONFIG`, then checkout `default.toml` / caller `dogpdkb.toml`.
`select_book(settings, key=None) -> Book` requires one enabled source.

- `Book`: `key`, `name`, `url`, `corpus`.
- `Settings`: `file`, `books_file`, `corpus`, `staging`, `state`, `web`,
  `graph_dir` (optional), `mcp_url`, `proxy_bin`, `timeout_seconds`,
  `ocr_budget_seconds`, `ocr_cache`, `results`.
- `settings.graph_for(book)` returns configured graph directory or the book's
  `graphify-out`; `settings.command(name)` returns an argv list from `[tools]`
  (`mdcompare`, `mdocr`, `graphify`, `skylark`; strings mean an executable,
  not shell text). Missing entries default to the command name.
- Existing `[source] books_file`, `[paths]` and `[skylark]` sections remain
  convenient configuration, without importing mdocr's configuration model.

## Download scheduling

`sync` and `apply` accept the runtime option `--concurrency` with integer choices
1, 2 and 3, default 2. It is not a Settings/TOML field and does not participate in
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
Reusing the same key for a different source book URL is rejected.
`store.data` is a plain dictionary with `schema`, `book`, `observation` and
`documents`. Product stages own observation and document facts and call
`store.save()` after factual changes.

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
Duplicate IDs fail before any durable registration.

## Run record

`Run.create(store, command) -> Run` creates a new `run-*` directory under
`settings.staging / book.key`, regardless of old interrupted records.
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

Run JSON is schema `dogpdkb-run-1`, containing run_id, command, book, pid, status,
stage, counts, started_at, updated_at, ended_at, exit_code, reason, artifacts and
failures. Status is observationally interrupted when `running` has no live owner.
`targets` freezes the source book URL and absolute corpus/graph targets. Loading
a run for execution rejects changed targets before any tool call or publication;
use a newly generated plan for a different source or target.
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
written only after complete publication and excludes `before_copy`.

A saved final compile manifest is reused even with pending optional OCR work;
resuming a run does not renew its bounded OCR budget. Existing failures/pending
reasons remain observable and usable bodies can proceed to incremental Graphify.
Explicit recovery of older failed runs belongs to the external agent; there is
no automatic historical migration or retry layer.

Cleaned completed/partial runs are not ordinarily resumable. Explicit
`sync --resume RUN --retry-graph` uses only a retained final compile manifest,
including after cleanup, and revalidates it through the graph stage. It never
re-enters discovery, download, comparison, OCR or corpus publication. Missing or
changed witnesses still require a new plan. Historical failures stay recorded;
the final result can remain partial even after successful graph recovery.
Recovery snapshots the already-cleaned run's retained file paths before the new
attempt, so cleanup preserves its failure bundles even when the old plan was
pruned. Newly created disposable graph workspace files can still be reclaimed.
Manual `purge` deletes only
selected wash; force permits discarding unfinished runs but never bypasses locks
or clears durable facts, corpus, graphs, persistent inputs or tool caches.
Purge validates saved schema, book key/source URL and run ID against the selected
wash; it does not require the saved corpus/graph execution targets to match the
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
json/watch. `view_run(path)` returns a read-only snapshot, with process/lock
liveness and elapsed seconds. Watch emits JSONL when combined with JSON.

## Automatic body routing and explicit reselection

Complete `HOLD_EXISTING` body advice produces `retain`; unavailable/incomplete
body comparison (including incomplete baseline evidence) produces `defer` and a
durable unknown comparison result. Neither invokes new OCR or promotes resource
uncertainty into full-source extraction. Strict KEEP remains reuse/metadata,
while concrete body candidates compile. Retained/deferred documents can publish
a usable corpus version without advancing graph extraction facts. A durable
`graph_baseline` descriptor preserves the previous full body outside wash, with
`path` to its witness and `source_file` relative to corpus; future comparisons
prefer the latest actual successful graph witness, then that preserved basis.
This prevents a newer raw export from silently becoming its own graph baseline.

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
