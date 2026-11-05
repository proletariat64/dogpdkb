
repo root

## data dir structure .gitignore

+- inbox/   raw knowledge materials before standardized
+- wiki/ llm wiki
 +- graphify_flatten_ready/  pre-processed knowledge markdowns
 +- graphify_output/  compiled llm graphic wiki
+- skylark_books/ skylark URLs and its sync records

## src

## mdocr — Markdown image prepare/OCR CLI

`mdocr` prepares images referenced by Markdown and can replace them with OCR
markdown from the PaddleOCR API. Bootstrap once with Python 3.12 and `uv`, then
activate the worktree-local environment:

```bash
make bootstrap
source .venv/bin/activate  # exposes the `mdocr` command
```

Run `make test` for the test suite and `make lint` for Ruff checks.

### Yuque Knowledge Base Sync

```bash
source ~/.bashrc

# Preview only; do not modify any data
.venv/bin/python scripts/sync_knowledge_base.py --dry-run

# Sync the corpus, Graphify graph, and Web pages
.venv/bin/python scripts/sync_knowledge_base.py

# Resume after a failure
.venv/bin/python scripts/sync_knowledge_base.py \
  --resume --staging <previous-run-directory>
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

All knowledge-sync entry points accept `--config <file>`. With no input they
read the repository's `default.toml` (independent of the current directory).
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
