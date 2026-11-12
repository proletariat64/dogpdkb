# CLI orchestrator rewrite acceptance

Date: 2026-11-12
Branch: rewrite/orch-delivery
Integrated prerequisite tip: 46b46f1acb5e0450b79119523a986734ec9d44f6
Review status: candidate ready; independent reviews pending; ticket 07 remains claimed.

## Repository and installed proof

| Layer | Observed result |
| --- | --- |
| Complete repository gate | `PYTHONPATH=/tmp/dogpdkb-orch-delivery/src make check`: doc-guard 107 documents, Ruff, 203 formatted Python files, **782 tests passed** in 61.49 seconds |
| Graph baseline supplement | Merged after that full gate; the six orchestrator contract files, including boundary guards and all changed runtime paths, pass **58 tests** in 23.48 seconds |
| Final style checks | Ruff and format checks pass after the supplement |
| Build | `make build DIST_DIR=/tmp/dogpdkb-orch-delivery-dist` produced wheel and sdist using the existing offline uv cache |
| Installed smoke | Disposable environment installs that wheel, verifies its import location, portable config/help/status, plan pause/resume and serial stage order; real installed mdcompare and image-less mdocr CLI; external Skylark/Graphify fixtures |
| Foreign-runtime boundary | Static/dynamic tool imports and helper-script bypass guards pass; no obsolete pipeline runtime remains in the wheel/sdist |
| Paid OCR, paid Graphify extraction, production UAT, deployment/release | **Not run** |

All development checks explicitly select this worktree's `src` through
`PYTHONPATH`; the reused Python 3.12 environment's editable install points at the
original checkout. Smoke removes that override and confirms the installed wheel
is imported from its disposable environment. Shared environment packages were
not pruned or reinstalled. Build/isolated install used existing cache access.

The final wheel and source archive are under
`/tmp/dogpdkb-orch-delivery-dist/`; no software release was published. Independent
Graphify/Skylark upstream changes remain explicit installation prerequisites,
not globally installed tools or production proof.

## Focused acceptance added during delivery

- Public CLI signals return 130/143 and stop the tool process group, including
  its child. Actual corpus and graph targets reject concurrent writers; the next
  cron acquires them after interruption. JSONL watch preserves file bytes and
  modification times and stops with 130.
- Registered state rejects a changed source book URL. Paused runs freeze source
  URL and absolute corpus/graph targets; target changes fail before OCR or
  publication. Imported differences require the selected book URL.
- Failed v1, followed by failed v2, still suppresses v1 after all run directories
  are removed. Minimal durable per-version blocking facts retain that truth.
- Resuming a paused run clears its terminal fields; live elapsed time advances.
- A gateway per-call timeout does not terminate a complete listing batch.
  Tool logs retain masked command headers; credential values are redacted.
- Existing graph inventory matches a registered baseline before a reusable
  source can acknowledge metadata or advance graph truth. Unknown versions do
  not become invented successes.

The initial full gate exposed three stale assertions/fixtures: lifecycle usage
expected an exception instead of exit 64; distribution omission tested deleted
`knowledge_*` modules; the installed fake invoked a nonexistent `mdocr.__main__`.
The tests now check the current serial runtime and the public `mdocr.cli` entry.
No product behavior was weakened to satisfy those fixtures.

## Physical line scope

Counts include comments and blank lines, without moving or compressing code.

| Scope | Lines |
| --- | ---: |
| Every Python module under `src/dogpdkb/`, including retained Web/lifecycle | 3,428 |
| Runtime helpers: `scripts/process.py` (30), `publish_web.py` (14), `smoke_install.py` (269) | 313 |
| Total orchestrator and operational runtime | **3,741** |
| Development/release helpers: bootstrap, doc guard, install shell, release/publish scripts | 1,447 |
| Independent mdcompare research/UAT scripts | 1,628 |
| Independent `src/mdocr/` and `src/mdcompare/` Python source | 5,195 |
| All Python and CJS tests | 9,779 |
| Orchestrator contract and boundary tests (`test_orchestrator*.py`) | 1,885 |
| Independent tool tests (tool prefixes or direct tool imports, including packaging guard) | 5,217 |

The remaining 2,677 test lines cover engineering, packaging, Web and lifecycle.
The runtime is below the specification's 4–6k estimate; no extra framework was
added to fill a line budget. Static Web frontend assets are an independent
product surface and are outside the orchestrator count.

Reproduce the primary source count with all `src/dogpdkb/**/*.py` plus the three
listed runtime scripts; use physical `splitlines()`/`wc -l`, not AST statements.
The source module set includes the actual apply, graph, configuration, records,
CLI, observation, process, Web and lifecycle implementations.

## Independent tool delivery

- mdocr batch and mdcompare catalog: repository feature `4e85aea`; public tool
  CLI tests and real local Markdown/image fixtures, without paid OCR.
- Graphify: independent source commit
  `ffd2eafef6b161d8c72a03dcdf1574d3439d3d8b`; reproducible patch and exact source
  identity in `docs/upstream/graphify-source-update/`; public subprocess tests
  include a localhost provider. The user's installed Graphify was untouched.
- Skylark: independent source commit
  `0e7c21883481d04a42a771c6fad1ddd5e6e0aa91`; reproducible patch in
  `upstream/skylark-listing-export-cli.patch`, fake MCP and real Markdown tests.
  No authenticated gateway request was made.

The contracts document how to select these public tool executables. Upstream
release publication, operator installation, real paid processing and production
validation remain distinct from this repository and isolated-install proof.
