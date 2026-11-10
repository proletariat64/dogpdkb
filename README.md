# dogpdkb

The deliverable is the complete **dogpdkb knowledge synchronization platform**:
Yuque discovery/export, image preparation/OCR, incremental graph updates, and Web
data publication. The `mdocr` command is an included component, not a separate
release unit. See [platform installation and preflight](docs/platform.md).

## Server installation

The [product homepage](http://poolab-iys595a7znqgza1e.inc.alipay.net/kb/#install)
shows a copyable Linux installation command and links to AntCode Releases. It
explicitly distinguishes preview packages from stable versions.

Published Releases include a version-bound installer. On Linux, replace `v0.1.1`
with an existing Release tag:

```bash
curl -fsSL https://code.alipay.com/pd.pt/dogpdkb/releases/download/v0.1.1/install.sh | bash
```

No AntCode CLI or login is required on the server. Installer-managed deployments
from v0.1.1 provide explicit lifecycle commands:

```bash
dogpdkb update --version 0.1.2  # choose an existing published version
dogpdkb uninstall              # confirm interactively; --yes for automation
```

An upgrade builds and checks a fresh environment, switches to it, then removes the
previous managed environment. Uninstall removes managed program files and command
links, not configuration or user data. A v0.1.0 installation must first run the
new version's curl installer to gain these commands. See
[installation, paths and prerequisites](docs/installing.md).

## Repository layout

- `src/dogpdkb/`: installed platform CLI, doctor, configuration templates and workflows.
- `src/mdocr/`: included Markdown image preparation and OCR component.
- `src/mdcompare/`: standalone offline content comparison and OCR reuse advice.
- `scripts/`: development helpers and backwards-compatible workflow entry points.
- `tests/`: offline automated tests.
- `web/kb/`: product homepage and independent installation/release panel.
- `docs/`: engineering documentation.
- `skylark_books/`: source registry and legacy source reference.
- `inbox/`: incoming knowledge materials.
- `graphify_flatten_ready/`: local corpus and graph artifacts, ignored by Git.

## mdcompare — offline content comparison CLI

`mdcompare compare` independently compares captured raw Markdown, resources and
OCR reuse evidence before processing. It emits versioned JSON, bounded optional
differences and conservative UNKNOWN decisions; it does not run a sync or OCR.
See the [mdcompare guide](docs/mdcompare.md), [evidence contracts](docs/mdcompare-contract.md)
and [real-file UAT record](docs/mdcompare-uat.md). It is included in the wheel.
For the opt-in daily raw snapshot, review and approved whole-document workflow, see
[daily knowledge sync](docs/daily-knowledge-sync.md) and its
[isolated UAT](docs/daily-knowledge-sync-uat.md). Existing commands remain unchanged
unless `--snapshot` or `sync --knowledge` is explicitly selected. Existing cleaned
corpora can be registered with `dogpdkb init-state --config dogpdkb.toml --book <key>`;
knowledge capture and planning then save each completed step in the corpus
`.sync-state.json`. See [state, UTC+8 and single-updater behavior](docs/sync-state-design.md).

## mdocr — Markdown image prepare/OCR CLI

`mdocr` prepares images referenced by Markdown and can replace them with OCR
markdown from the PaddleOCR API. Bootstrap once with Python 3.12 and `uv`, then
activate the worktree-local environment:

```bash
make bootstrap
source .venv/bin/activate  # exposes `dogpdkb`, `mdocr` and `mdcompare`
```

Run `make check` before pushing: it runs `make doc-guard`, `make lint`, and
`make test`. Each command can also be run independently. See
[the document and CI rules](docs/doc-guard.md) for scope and conventions.

Use `make build` for the platform wheel/source distribution, `make smoke` for
an isolated installation test, and `make doctor` for offline operational preflight
(or `make doctor DOCTOR_ARGS=--package` for package-only checks).

GitHub is the source repository: development and version changes go through a PR
and merge into `master`. From the clean, synchronized `master` checkout, run
`make release` to publish the already-reviewed package version. It pushes only the
source tag to GitHub; AntCode hosts installation attachments, not a copy of the
source branch. Preview with `make release DRY_RUN=1`. See
[local release and retry](docs/releasing.md). Publication uses the local AntCode
login and finishes by updating the homepage through `poolab`; see
[homepage delivery](docs/homepage.md).

GitHub Actions runs `make check` and `make smoke` on every push and pull request,
after bootstrapping Python 3.12 with the committed `uv.lock`. The checks do not
require Yuque/PaddleOCR credentials or local corpus/graph artifacts. To block
merges, configure the repository's branch rules to require the `check` status;
adding a workflow alone does not enforce branch protection.

### Yuque Knowledge Base Sync

```bash
source ~/.bashrc

# Discover a plan only: does not publish corpus/graph/Web, but writes staging files
dogpdkb sync --dry-run

# Sync the corpus, Graphify graph, and Web data
dogpdkb sync

# Resume after a failure
dogpdkb sync --resume --staging <previous-run-directory>
```

Supports Doc, Sheet, Table, and image OCR. Incremental updates only; no full graph rebuild.

Usage:

```bash
mdocr -h                    # top-level help
mdocr ocr -h                # per-command help

mdocr prepare -f doc.md -o out/          # normalize local images to JPEG assets
mdocr ocr -f doc.md -o out/              # replace images with OCR markdown
mdocr ocr -f a.md b.md -o out/ --download  # multiple files + fetch remote images
mdocr prepare -f doc.md -o out/ --force  # overwrite existing outputs
```

Notes:

- Input files are never modified; outputs go to `-o DIR` (`<name>.md` plus
  `assets/<name>/` JPEG copies, links rewritten).
- Remote images are skipped with a warning unless `--download` is given.
- `ocr` reads the `PADDLEOCR_TOKEN` environment variable for the PaddleOCR API.

### PaddleOCR result download — bcebos CDN workaround

`mdocr ocr` downloads each OCR result JSONL from PaddleOCR's BOS bucket
(`paddleocr-store-N.bj.bcebos.com`). The job's result URL randomly picks a
`store-N` name, and the public DNS round-robin routes most of them
(`store-2..8`) to a dead CDN edge (`103.235.47.176`) that accepts TCP but
stalls reads, producing `Read timed out. (read timeout=60)` after the job
already reached `done`.

This box pins all eight `store-N` names to a healthy edge in `/etc/hosts`:

```text
182.61.129.11 paddleocr-store-1.bj.bcebos.com
182.61.129.11 paddleocr-store-2.bj.bcebos.com
... (through paddleocr-store-8.bj.bcebos.com)
```

`182.61.129.11` serves every `store-N` name (same bucket/region, verified
0.1 s responses). hosts outranks DNS, so result downloads bypass the dead edge.

Additionally, run OCR with the BOS domain **excluded from the proxy**, because
the local sing-box proxy re-resolves and can still land on the dead edge:

```bash
export NO_PROXY="$NO_PROXY,bj.bcebos.com"
mdocr ocr -f doc.md -o out/
```

Notes:

- `requests` matches `NO_PROXY` by bare domain — `*.bj.bcebos.com` does **not**
  match, use `bj.bcebos.com`.
- Verify health: `curl -sS -o /dev/null -w '%{http_code}\n' --noproxy '*' \
  --resolve 'paddleocr-store-2.bj.bcebos.com:443:182.61.129.11' \
  https://paddleocr-store-2.bj.bcebos.com/` (expect `403`, bucket root).
- To revert: delete the `paddleocr-store-*` lines from `/etc/hosts`.
- If Baidu rotates edges, re-verify the pinned IP before mass OCR runs.

### Configuration and book registry

All knowledge-sync entry points accept `--config <file>`. In a source checkout,
with no input they read the repository's `default.toml` (independent of the current
directory). A wheel installation instead reads `./dogpdkb.toml` from the caller's
working directory; `dogpdkb init-config` creates portable, disabled examples.
`DOGPDKB_CONFIG` can select another file; an explicit `--config` wins. A selected
file replaces `default.toml`, rather than merging with it.

Operational precedence is CLI arguments, then environment overrides, then TOML,
then portable program defaults. Relative paths in TOML are based on that file's
directory; CLI and environment paths are based on the working directory. `~` is
expanded. Credentials such as `PADDLEOCR_TOKEN` stay in the environment.

```bash
# Uses default.toml and its single enabled book
.venv/bin/python scripts/sync_knowledge_base.py --dry-run

# Local deployment overrides: copy default.toml, edit paths/gateway, then run
cp default.toml config.local.toml
.venv/bin/python scripts/sync_knowledge_base.py --config config.local.toml \
  --book fund-platform --dry-run

# Existing explicit parameters still override config
.venv/bin/python scripts/sync_knowledge_base.py --config config.local.toml \
  --input ./corpus --skylark-url https://example.invalid/team/book \
  --web-dir ./site --dry-run
```

`default.toml` currently contains this project's existing gateway, image URL
mapping and Web classification rules. It is configuration, not a sanitized public
template. Set `[paths].web` explicitly for a deployed Web server; the portable
default writes to local `web-output/<book-key>/`. Set `graphify_python` when
Graphify lives in another environment; otherwise the current interpreter is used.

Web publication writes both JSON files into a generation under `.snapshots/`,
then atomically switches a shared `current` symlink. The public `catalog.json`
and `map/map-data.json` paths remain unchanged and become relative symlinks;
the static server must follow symlinks. Existing ordinary files are preserved
in an initial generation, and older generations are retained for recovery.

The source list is `skylark_books/roots.json`, referenced by
`[source].books_file`. It is an input registry, not an automatically discovered
list of all accessible Yuque books. Example entry:

```json
[
  {
    "key": "fund-platform",
    "name": "Example knowledge base",
    "url": "https://example.invalid/team/book",
    "enabled": true,
    "corpus": "../graphify_flatten_ready"
  }
]
```

Each key must be unique and safe as a directory name. With multiple enabled
books, `--book <key>` is required; `--book` and `--skylark-url` are mutually
exclusive. A book's optional `corpus` is relative to the registry file. Without
it, the corpus is `<paths.corpus>/<key>`; the existing book keeps its current
corpus through an explicit entry. `--input` or `DOGPDKB_CORPUS` overrides the
selected corpus. `roots.md` is a legacy reference, not a second configuration.

Captured book ID, name, source URL, document totals and listing completeness are
written in each `sync-updates-*.json` (`get_sync_updates.py --json`, always on in
the pipeline). Standalone discovery defaults to `<paths.state>/<key>/`;
standalone apply reads that location. Pipeline plans/checkpoints stay together
under `<paths.staging>/<key>/sync-<timestamp>/`. Both locations are ignored by Git;
`.sync-state.json` stays inside each corpus to retain incremental timestamps.
An ad-hoc `--skylark-url` uses the `adhoc` key: provide separate `--input`,
`--staging` and `--web-dir` when operating multiple ad-hoc sources.

The pipeline forwards `--config` to every child entry point and records effective
non-secret settings in its checkpoint. Resume rejects changed configuration;
older checkpoints without configuration metadata require a new staging run.
Read-only MCP requests retry recognized temporary timeouts, rate limits, and
service/network failures with bounded exponential backoff and jitter.
`skylark.max_retries` counts retries after the initial attempt. Authentication,
permission, parameter, and unknown failures stop without that retry loop. Logs
distinguish the local wait deadline from a timeout returned by the remote call;
a returned `Request timeout for 15000 ms` does not describe the local timeout
setting.

On failure or cancellation, keep the staging directory and use the printed
`Retry:` command with the same configuration and environment. An incomplete
listing is fetched in full on resume: saved offset pages are diagnostic files,
not a consistent remote snapshot. A completed plan is reused. For that fixed
plan, valid export and OCR checkpoints are reused; missing artifacts are redone.
Resume does not request every completed document's latest remote version.

Required export or OCR failures leave a failed diagnostic manifest and prevent
corpus publication, planned deletion, sync-state updates, and graph/Web stages.
This also applies to direct `apply` and `apply --no-merge`; an empty new document
may be explicitly skipped, while an existing document becoming empty is updated.
After successful recovery, publication uses the whole fixed batch. Once
publication begins, disk errors or cancellation may leave some files published;
there is no transaction across the entire corpus and no automatic rollback of
partial batches published by older versions.

Graph updates also pin the original graph artifacts before extraction, build
from that backup, and compare bytes before publication. A staging directory from
before this baseline checkpoint was introduced must be replaced by a new run.
If publication fails because another publisher changed the graph baseline, keep
the staging directory and retry with `--resume --rebase-graph --staging <run>`.
This reuses the completed corpus manifest and rebuilds only its graph delta
against the current graph, then republishes the Web snapshot. Starting fresh
discovery instead can miss changes whose corpus timestamps already advanced.

| Environment override | TOML key |
| --- | --- |
| `DOGPDKB_BOOKS_FILE` | `source.books_file` |
| `DOGPDKB_CORPUS`, `DOGPDKB_STAGING`, `DOGPDKB_STATE`, `DOGPDKB_WEB` | corresponding `paths` key |
| `DOGPDKB_GRAPH_DIR`, `GRAPHIFY_PYTHON` | `paths.graph_dir`, `paths.graphify_python` |
| `SKYLARK_MCP_URL`, `UTOO_PROXY_BIN` | `skylark.mcp_url`, `skylark.proxy_bin` |
| `SKYLARK_TIMEOUT_MS` | `skylark.timeout_seconds` (environment value is milliseconds) |
| `SKYLARK_MAX_RETRIES`, `SKYLARK_CONCURRENCY` | corresponding `skylark` key |

Unknown keys, malformed URLs and invalid numeric settings fail before sync work.
The MCP protocol version, API page sizes and graph safety checks remain program
rules. PaddleOCR provider defaults remain owned by the existing `mdocr` CLI.
