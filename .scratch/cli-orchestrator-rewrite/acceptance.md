# CLI orchestrator rewrite acceptance

Date: 2026-11-13
Branch: rewrite/cli-orchestrator
Review base: 2da50e218148a4f3530c789abbdfa45939d5f4f3
Review status: complete. Standards/Spec findings and all 21 automated OCR findings resolved; final complete gate and rebuilt installed-wheel smoke pass. All eight tickets resolved.

## Repository and installed proof

| Layer | Observed result |
| --- | --- |
| Complete repository gate | `PYTHONPATH=/tmp/dogpdkb-orch-final-fixes/src make check`: doc-guard 109 documents, Ruff, 205 formatted Python files, **811 tests passed** in 67.62 seconds |
| Focused review corrections | **99 tests passed** in 31.75 seconds across apply/graph/operations, tools, lifecycle, Web and process cleanup |
| Final style checks | Ruff and all 205 format checks pass after every runtime correction |
| Build | Final reviewed runtime: `make build DIST_DIR=/tmp/dogpdkb-orch-reviewed-dist` produced wheel and sdist using the existing offline uv cache |
| Installed smoke | Final reviewed wheel smoke passed: disposable environment installs that wheel, verifies its import location, portable config/help/status, plan pause/resume and serial stage order; real installed mdcompare and image-less mdocr CLI; external Skylark/Graphify fixtures |
| Foreign-runtime boundary | Static/dynamic tool imports and helper-script bypass guards pass; no obsolete pipeline runtime remains in the wheel/sdist |
| Paid OCR, paid Graphify extraction, production UAT, deployment/release | **Not run** |

All development checks explicitly select this worktree's `src` through
`PYTHONPATH`; the reused Python 3.12 environment's editable install points at the
original checkout. Smoke removes that override and confirms the installed wheel
is imported from its disposable environment. Shared environment packages were
not pruned or reinstalled. Build/isolated install used existing cache access.

The final wheel and source archive are under
`/tmp/dogpdkb-orch-reviewed-dist/`; no software release was published. Independent
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
| Every Python module under `src/dogpdkb/`, including retained Web/lifecycle | 3,531 |
| Runtime helpers: `scripts/process.py` (54), `publish_web.py` (14), `smoke_install.py` (275) | 343 |
| Total orchestrator and operational runtime | **3,874** |
| Development/release helpers: bootstrap, doc guard, install shell, release/publish scripts | 1,447 |
| Independent mdcompare research/UAT scripts | 1,628 |
| Independent `src/mdocr/` and `src/mdcompare/` Python source | 5,267 |
| All Python and CJS tests | 10,331 |
| Orchestrator contract and boundary tests (`test_orchestrator*.py`) | 2,173 |
| Independent tool tests (tool prefixes or direct tool imports, including packaging guard) | 5,383 |

The remaining 2,775 test lines cover engineering, packaging, Web and lifecycle.
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

## Final review fixes

The [two independent review reports and resolutions](review.md) remain separate.
Standards found one administrative tracker issue; Answer sections and linked map
results are now present. Spec found two reproduced P2 issues: opaque graph version
ordering and raw input mutation during an earlier OCR call. Both have public CLI
red→green regressions and pass the complete gate. The manifest retains its frozen
prior graph fact; raw inputs are checked before/after OCR and all plan inputs
are checked before publication. Changed in-flight work records an unknown outcome
and preserves evidence. Automated OCR reviewed all 22 selected files with zero failed files; its 21 high/medium findings are corrected and recorded individually in review.md. The final complete gate covers all corrections; earlier 789-test/build results are historical, not final proof.

Independent follow-up verification also proved:

- two disjoint managed syncs can run concurrently while update/uninstall/direct
  installer are excluded; inherited tool descriptors keep the installation lock
  after parent termination until tool shutdown;
- apply and sync persist interruption facts under actual target locks;
- known successful graph input/action/metadata acknowledgements do not skip a
  changed same-run OCR-enriched source, and unknown pending discovery is retained;
- day-only init-state baselines allow precise same-business-day updates while
  provably older timestamps remain refused;
- with real local PNG/Markdown and public mdcompare/mdocr, missing cache records
  cause necessary OCR and retain recognized text; the next version reuses cache
  without a token or another HTTP submission.

All provider interactions above used localhost test services. The complete gate
includes the foreign-import/helper bypass guards with **zero violations**.

The final wheel and sdist were rebuilt after every review correction. Direct byte
comparison found all **43 packaged runtime Python modules** identical to the final
source worktree in both archives. The final smoke installed that wheel into a
disposable environment and passed public plan/resume, real mdcompare and image-less
mdocr, with external Skylark/Graphify process fixtures. Build log:
`/tmp/dogpdkb-reviewed-build.log`. No global package installation or release occurred.

## Reproduce from the retained integration worktree

The commands above record their observed worker paths. Implementer worktrees
are removed after integration. From `/tmp/dogpdkb-cli-orchestrator-integration`,
reuse the existing Python environment without a checkout-local `.venv`:

```bash
rtk proxy env PYTHONPATH="$PWD/src" make DEV_PYTHON=/home/ubuntu/dev/dogpdkb/.venv/bin/python check
```

`/tmp/dogpdkb-orch-reviewed-dist` and the independent tool deliverables remain
available after worker cleanup. This command validates the retained integration
source rather than the original checkout's editable installation.
