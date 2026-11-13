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
Cleaned completed/partial runs are not resumable. Manual `purge` deletes only
selected wash; force permits discarding unfinished runs but never bypasses locks
or clears durable facts, corpus, graphs, persistent inputs or tool caches.

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
