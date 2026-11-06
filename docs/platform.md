# dogpdkb platform delivery and preflight

## Delivery boundary

The Python distribution is `dogpdkb`. It includes:

- `dogpdkb sync`: resumable discovery, export/OCR, corpus, graph and Web-data pipeline.
- `dogpdkb discover`, `apply`, `update-graph`, `publish-web`: individual workflow stages.
- `dogpdkb doctor`: offline preflight; `dogpdkb init-config`: safe example initialization.
- `mdocr prepare` and `mdocr ocr`: the platform's image component.
- Portable configuration and book-registry templates.

Runtime workflows live in `src/dogpdkb/workflows/`. Existing `scripts/*.py`
workflow entry points remain compatible in source checkouts. The old editable
distribution name `mdocr` is replaced by `dogpdkb`; both executable names are
provided by the new distribution. Existing independent mdocr installations are
not automatically removed or upgraded.

Supported runtime: Python 3.10+ on POSIX; development/CI use Python 3.12.
The current graph publisher uses POSIX file locks, so native Windows is not
supported; use WSL2. This build does not create a new dashboard frontend or Web
server: the existing publisher produces catalog/map JSON for a separate server.

## Build and install

For anonymous Release installation and upgrades without a checkout or AntCode
CLI, use [the Linux curl installer](installing.md). The commands below remain
available for manual installation and development.

From a checkout with Python 3.12 and uv:

```bash
make bootstrap
make check
make build
make smoke
```

`make build` uses uv/setuptools to create `dist/dogpdkb-<version>.tar.gz` and
`dist/dogpdkb-<version>-py3-none-any.whl`. The wheel is built from the source
distribution, checking that packaged source and resources are sufficient.
Build outputs are ignored by Git. No release is uploaded.

Install the wheel into your chosen virtual environment, not the system Python:

```bash
uv venv ~/venvs/dogpdkb --python 3.12
uv pip install --python ~/venvs/dogpdkb/bin/python dist/dogpdkb-0.1.0-py3-none-any.whl
source ~/venvs/dogpdkb/bin/activate
dogpdkb --version
dogpdkb doctor --package
```

The wheel does not include production configuration, credentials, Yuque source
registries, corpus Markdown, graphs, staging/checkpoint state, or a Graphify
environment. Installing it does not perform historical extraction or synchronization.

## Configure an installed platform

```bash
mkdir -p ~/dogpdkb-instance
cd ~/dogpdkb-instance
dogpdkb init-config
# Edit dogpdkb.toml and books.json before enabling your actual source.
dogpdkb doctor --config ./dogpdkb.toml --book <book-key> --require-ocr
```

Initialization refuses to overwrite either existing file, including symlinks.
Example sources are disabled and use reserved `example.invalid` addresses.
Configuration precedence remains explicit `--config`, `DOGPDKB_CONFIG`,
then the default file. A source checkout defaults to repository `default.toml`;
a wheel installation defaults to the caller's `./dogpdkb.toml`. Relative paths
are anchored to the selected file, never to installed package resources.

Operational prerequisites are separate from the platform wheel:

- A configured utoo-proxy executable and Yuque gateway with working authentication.
- An existing selected corpus and Graphify `graph.json` baseline; the platform
  does incremental updates, not automatic historical backfill or full rebuilds.
- A Graphify/NetworkX environment exposing the exact APIs used by the updater,
  selected through `paths.graphify_python` or `GRAPHIFY_PYTHON`. Graphify is not
  automatically installed or upgraded: this pipeline uses version-sensitive private
  APIs. The updater adds its platform source/package location as an import fallback
  when running under a separate graph interpreter, without shadowing that
  interpreter's own dependencies.
- A configured Graphify LLM backend for uncached extraction.
- `PADDLEOCR_TOKEN` when image OCR is needed.
- Writable corpus, graph, staging, state and Web output locations. The Web server
  must follow the publisher's generation symlinks.

## Doctor contract

`make doctor` is equivalent to `python -m dogpdkb doctor`.
Pass options through `DOCTOR_ARGS`, for example:

```bash
make doctor DOCTOR_ARGS=--package
dogpdkb doctor --config ./dogpdkb.toml --book <book-key> --json
```

- `--package` checks included modules, runtime imports and templates only.
- Normal mode also validates config/source selection, proxy availability, corpus
  and graph baseline, writable ancestors, Graphify API imports and backend selection.
- Missing OCR credentials are a warning unless `--require-ocr` is supplied.
- Exit 1 means at least one failed check; exit 0 can still contain warnings.
- JSON output contains `ok` and individual `checks` with pass/warn/fail status.
- Token values, provider output and configuration contents are not printed.
- No directories/data are created, no graph extraction/rebuild runs, and no
  authentication or connectivity requests are made. The configured graph
  interpreter is probed in a bounded subprocess. Offline success is not proof
  that remote credentials or services work.

## Installation smoke test and CI

`make smoke` builds artifacts, checks their contents, and installs the wheel plus
locked runtime dependencies into a disposable environment outside the checkout.
It clears source-path/configuration overrides and verifies imports come from that
environment, not the repository.

It exercises both executable names, all workflow help entry points, package doctor,
safe initialization/refusal to overwrite, expected operational-not-ready reporting,
Web JSON publication from a tiny synthetic graph, and local-image preparation.
Only dependency installation/building needs network access; no Yuque, OCR, LLM,
production corpus or production output is used. Temporary installation data is
removed when the test finishes.

GitHub Actions runs `make check` then `make smoke` under its existing `check`
job. GitHub owns source PRs, checks and merges; AntCode only hosts installation
attachments. Release builds and uploads run locally; see [releasing](releasing.md).
Branch protection remains
a separate administrator setting. The Release installer performs isolated install
and upgrade; `dogpdkb update --version X.Y.Z` and `dogpdkb uninstall` manage only
installer-owned deployments. Successful upgrades discard the previous environment;
configuration and user data remain separate. Build/preflight commands do not deploy
services. Scheduler, Nginx and rollback management remain outside this scope.
