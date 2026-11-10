# Sync state baseline acceptance

Date: 2026-11-10. Scope: source implementation, isolated installed behavior and existing real-file initialization in task-owned copies. Production deployment, production migration and paid extraction are not part of this acceptance.

## Implemented behavior

- `init-state` accepts existing cleaned Markdown, an existing Graphify graph and the selected books configuration. It writes canonical `.sync-state.json` v2 without requiring historical raw registration or recompiling the graph.
- Initialization reports all root Markdown files, recognized document identities, files without a standard identity, duplicate IDs and graph source matches. Files without a standard ID remain in `unmatched_files`, without invented Yuque identities.
- Canonical capture and `sync --knowledge --config ... --book ...` persist completed business steps in the corpus state. Observed, downloaded, compared and applied versions remain separate. Failed or pending graph work is retained when remote timestamps do not change.
- UTC+8 is the default for normalized timestamps, filename dates and cutoff selection. The compatibility key `utc` retains its name but stores an offset-aware `+08:00` value. Original timestamp fields and values are retained; unknown historical instants are not manufactured from filename dates.
- All mutating CLI/workflow entry points share the user instance lock. Real process regression tests cover different configurations/books, direct workflow wrappers, invalid inherited descriptor hints and parent termination while the child still holds the lock. Doctor, help and standalone mdcompare remain available.
- Legacy explicit standalone knowledge state is compatible. Import into configured canonical state writes a redirect marker in the old state file; old plans and their bound approvals/quality files must be regenerated.

## Automated and installed checks

`make check` passed: document guard, Ruff checks/format and 1,004 tests in 100.73 seconds. The new unmatched-file test first failed with `files == 1` for two root Markdown inputs, then passed after inventory reporting was corrected.

The first installed smoke exposed an import-order regression: the isolated Graphify interpreter could not import `dogpdkb` before the existing workflow package-path fallback ran. The imports were moved after that fallback. After this fix, the final graph/knowledge execution/initializer regression subset passed: 66 tests in 27.99 seconds. Documentation and lint checks were repeated after the acceptance report was added.

Final `make smoke` passed using the built wheel and source distribution in disposable environments. It checks package ownership, command availability, legacy knowledge planning and explicit execution with a deterministic Graphify boundary, rejected quality cases, and installed lifecycle behavior. The added canonical smoke also checks:

1. An installed `init-state` is rejected while the user updater lock is held, before writing corpus state.
2. Existing historical Markdown initializes with an explicitly reported unmatched local file.
3. An initialized same-day baseline is downloaded through the complete offline listing/details adapters; `2026-11-09T18:30:00Z` is saved as `2026-11-10T02:30:00+08:00` immediately after download.
4. Canonical `sync --knowledge` reuses unchanged body knowledge, records comparison completion and advances the reuse coverage version, while leaving graph bytes unchanged.

These installed inputs are controlled development fixtures. The Graphify boundary is deterministic and does not call a real model; its successful execution is not evidence of production knowledge completeness or real model costs.

## Real existing-corpus initialization

The real-file exercise used independent copies under `/tmp/dogpdkb-sync-state-real-uat-ahcz9q86`. The source was `/home/ubuntu/dev/dogpdkb/graphify_flatten_ready`; files were copied rather than hard-linked. The final `init-state` output reported:

| Inventory | Count |
| --- | ---: |
| Root Markdown files | 4,782 |
| Recognized unique document IDs | 4,781 |
| Files without a standard ID | 1 |
| Recognized documents with graph source correspondence | 4,778 |
| Recognized documents with unknown graph source correspondence | 3 |
| Recognized documents with only day precision | 4,781 |

The source has no `.sync-state.json`; this exercise created state only in the task-owned copy. All 4,782 copied Markdown files remained byte-identical to the source. Both copied `graph.json` and Graphify `manifest.json` remained byte-identical to the source. Unknown source coverage and the no-ID file remain explicit; initialization success does not certify that the graph contains every fact from a document.

Private evidence in that directory: `final-initialization.stdout`, `final-initialization.stderr`, and `final-verification.json`. Gate logs: `/tmp/dogpdkb-sync-state-04-check.log`, `/tmp/dogpdkb-sync-state-04-final-focused.log`, and `/tmp/dogpdkb-sync-state-04-smoke.log`.

## Remaining delivery boundary

No production corpus, graph or co.bot state was migrated or updated. No live remote listing, full production mdcompare run, real Graphify model execution, paid extraction or production Web publication was performed. Final standards/specification review and the separate OCR CLI review are distinguished below.

## Integration review fixes and final gates

The initial standards/specification reviews identified four issues, each reproduced through the existing CLI seams before correction:

1. Remote page observations created state rows that were mistaken for existing local sources, downloading intentionally omitted historical documents. Existing-source selection now requires actual baseline/corpus/download/graph source evidence; observed-only rows, including rows from previous attempts, still obey the historical cutoff.
2. Configured retries claimed the run before checking a completed execution or committed journal. Read-only ownership validation now precedes completion recovery; only unfinished execution persists a new claim. Repeated execution and missing-report recovery preserve committed state/graph bytes and finish with no active run.
3. Explicit legacy-state redirect execution did not validate canonical ownership and could clear another active plan. All canonical execution routes now validate the same snapshot, book and plan ownership before writing.
4. Legacy apply could register listing time as detail time and ignore detail `updated_at`. The actual detail field/value is retained separately from listing version, carried through corpus and graph metadata, and missing/mismatched versions remain pending. Such manifests cannot publish corpus or graph changes. Discovery plans retain actual listing version metadata alongside compatibility fields.

Confirmed preliminary OCR findings added five narrow corrections:

- Required execution arguments and module availability are checked before initialization/import or plan claims.
- Legacy imports refuse to overwrite canonical observations, captured sources, pending work or step evidence.
- Quoted home paths are expanded consistently when binding capture/plan identities.
- Legacy pipeline resume/configuration validation runs before claiming a new active plan; rejection preserves any pre-existing run identity.
- Ordinary listing timestamp failures are explicit, retained as failed listing progress, and do not produce a successful zero-change plan. Missing, date-only and malformed values are not silently skipped or manufactured into exact instants.

Both standards/specification follow-up reviews found no remaining actionable issue in the corrected scope: standards ran 16 focused cases; specification ran 10 focused cases plus a missing-execution-module simulation. These are separate review results, not production acceptance.

Final post-fix `make check` passed document guards, Ruff checks/format and **1,021 tests in 125.10 seconds**. Final `make smoke` passed wheel/sdist and isolated installed workflows with a separate task-owned `XDG_STATE_HOME`. The earlier 1,004-test/66-test evidence above remains historical and is superseded for the final source revision by these gates. An intermediate four-fix revision also passed 1,013 tests and installed smoke; it is not the final-code gate.

Final gate logs: `/tmp/dogpdkb-sync-state-review-fix-final-check.log` and `/tmp/dogpdkb-sync-state-review-fix-final-smoke.log`. Red/green regressions are retained in `tests/test_sync_progress_cli.py`, `tests/test_apply_recovery.py`, `tests/test_pipeline.py` and `tests/test_update_graph.py`.

The OCR CLI review is still running at the time of this entry; only its confirmed preliminary findings above have been fixed. No final OCR pass is claimed. The existing real-corpus initialization proof remains limited to task-owned copies, and no production migration, remote sync, paid Graphify execution or Web publication was performed.
