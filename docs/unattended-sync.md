# Unattended daily sync deployment

`dogpdkb sync` runs the five batch stages (capture, mdcompare, plan, ocr,
compile) as one unattended batch. Scheduling is the operating system's job:
crontab starts the batch, the shell appends its output to a plain log file, and
the system logrotate rotates that file. The tool does not install cron entries,
does not run a scheduler or log-rotation daemon, and does not change your
crontab or logrotate configuration.

## Locations

| Purpose | Example location | Owner |
| --- | --- | --- |
| Program | `/home/ops/.local/bin/dogpdkb` | installer (`docs/installing.md`) |
| Configuration | `/home/ops/.config/dogpdkb/dogpdkb.toml` | operator |
| Book list | `/home/ops/.config/dogpdkb/books.json` | operator |
| Batch log | `/home/ops/.local/state/dogpdkb/sync.log` | shell redirection |
| Wash workspace | `[paths].staging` (default `wash`) | batch, cleanable |
| Durable results | `[paths].results` (default `sync-results`) | batch, never cleaned |
| Long-term OCR cache | `[paths].ocr_cache` (default `ocr-cache`) | batch, never cleaned |
| Published knowledge | `[paths].corpus` and its `graphify-out` | compile publication |

Replace `/home/ops` with the account that owns the corpus and the existing
credentials. Paths in the configuration file are relative to that file; the
crontab below uses absolute paths because cron does not expand `~`.

## Authentication

The runtime environment provides the existing authentication; the batch never
asks for credentials interactively and no credential belongs in a crontab line
or in a log file.

- The Yuque gateway uses the existing `~/.utoo-proxy/utoo-proxy` login of the
  account that runs the batch (`[skylark].proxy_bin`).
- OCR reads `PADDLEOCR_TOKEN` from the process environment only. It is not a
  configuration key and it is not written to results, logs or state.
- Graphify uses `[paths].graphify_python` (default: the current interpreter).

Cron starts with a minimal environment. Export `PADDLEOCR_TOKEN` through the
environment manager your account already uses (for example a systemd user
environment, an existing profile script sourced by a wrapper, or your secret
manager). If you prefer a wrapper, keep it outside the repository and let it
source the existing environment file before `exec`-ing the command below; never
echo the token. Without a token the OCR stage archives its images as anomalies
and the batch still finishes and publishes the usable set.

## crontab

One batch per book per day. `>>` appends and `2>&1` merges stderr (stage
progress, timestamps, diagnostics) into the same plain log as stdout (the
concise JSON batch result).

```cron
# m h dom mon dow command
30 2 * * * /home/ops/.local/bin/dogpdkb sync --config /home/ops/.config/dogpdkb/dogpdkb.toml --book fund >> /home/ops/.local/state/dogpdkb/sync.log 2>&1
```

Create the log directory once (`mkdir -p /home/ops/.local/state/dogpdkb`), then
install the line with `crontab -e` yourself. Stagger multiple books so two
batches never overlap: the user-level run lock refuses a concurrent batch, and
an unfinished run must be resumed instead of replaced.

Exit codes: `0` for completed, partial and a normal `--stop-after` pause; `1`
for a batch that cannot continue; `130` for a user interrupt. A partial batch is
a finished batch: it releases the run claim, so the next cron start is not
blocked. Never infer per-document success from the exit code alone.

## logrotate

The system logrotate owns rotation. Copy this stanza into
`/etc/logrotate.d/dogpdkb` (or your user-level logrotate configuration) and
adjust the path and retention:

```text
/home/ops/.local/state/dogpdkb/sync.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
```

`copytruncate` keeps the appending cron job writing to the same inode, so no
batch output is lost and no signal handling is needed. Check the stanza with
`logrotate -d /etc/logrotate.d/dogpdkb` before relying on it.

## Querying without touching the batch

```bash
dogpdkb status --config /home/ops/.config/dogpdkb/dogpdkb.toml --book fund
dogpdkb status --config /home/ops/.config/dogpdkb/dogpdkb.toml --json
dogpdkb anomalies --config /home/ops/.config/dogpdkb/dogpdkb.toml --book fund
```

`status` is read-only: it reports the current or most recent batch, its stage,
counts, timings, stop reason and log location, and it distinguishes a leftover
`running` record from a live process. Repeating it is the whole monitoring
contract; there is no watch mode or background service. `anomalies` scans the
durable results store and records version-scoped dispositions for the post-hoc
agent.

## Wash cleanup and durable evidence

Wash is batch workspace: paging, stage reports, plans and temporary compile
files. It is never the only home of a long-term original, a successful OCR
result, published knowledge or an anomaly recovery input.

- After every completed or partial batch, the runner removes earlier finished
  work directories under `<staging>/<book>/`, keeping the most recent finished
  batch and every paused or unfinished one.
- Before a directory is removed its durable result must exist, and any state,
  cache or anomaly reference to a unique file inside it is copied to
  `<results>/<book>/migrated/<run-id>/` and rewritten first.
- The outcome is recorded in `<results>/<book>/cleanup.json`. A cleanup failure
  is non-fatal: it never breaks a valid corpus, and the next finished batch
  retries it.
- Durable results, anomaly inputs, the long-term OCR cache and the canonical
  corpus/graph/state are never cleaned with wash. There is no automatic
  eviction policy for the cache or the anomaly archive.

## First tidy of historical wash

Historical work directories from the superseded pipeline are inventoried and
migrated, never bulk-deleted:

```bash
dogpdkb wash --config /home/ops/.config/dogpdkb/dogpdkb.toml --book fund --json
dogpdkb wash --config /home/ops/.config/dogpdkb/dogpdkb.toml --book fund --migrate
```

The inventory lists every work directory (unified, legacy, unrecognized), the
durable records that still reference files inside it, and the necessary
artifacts it holds. `--migrate` copies long-term originals, observation evidence
and legacy recovery records into `<results>/<book>/migrated/<run-id>/`, rewrites
the old references, and writes `<results>/<book>/wash-migration.json`. It deletes
nothing, and repeating it copies nothing new.

A legacy or unrecognized run is annotated in place (`wash-migration.json` inside
the directory) with `resume_verified: false`: a run paused under the previous
implementation is not assumed recoverable by the unified runner. Validate its
inputs and artifacts before attempting any recovery, and start a new batch
instead of resuming it when validation fails.
