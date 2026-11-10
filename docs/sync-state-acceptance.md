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

## Final OCR disposition and source closure

This entry supersedes the earlier pending-OCR statements. No external review process remains running. The original review targeted the branch change scope of 35 files but was manually interrupted after 38 minutes 15 seconds; 13 of the 15 source files had completed source checkpoints. A resume with a different scope was rejected by the OCR CLI. A separate bounded five-minute review of the two remaining source files completed `sync_time.py` with one low-severity note, while `sync_progress.py` timed out. The host then used the installed OCR delegate workflow's deterministic preview/rules and reviewed the full `sync_progress.py` diff and its consumers: one file reviewed, zero skipped.

Coverage therefore consists of **14 externally completed source reviews plus one host-delegated source review**. Neither external run is reported as an overall exit-0 review of all 35 changed files. The first external session is interrupted/partial, the bounded continuation is partial, and the delegated file review is a distinct proof layer. Reported external tokens are not Graphify extraction usage or production cost evidence.

The sanitized first-run source comments are retained in `/tmp/dogpdkb-sync-state-ocr-source-comments.json`, the bounded review result in `/tmp/dogpdkb-sync-state-ocr-remaining.json`, and the deterministic delegate scope plus host findings in `/tmp/dogpdkb-sync-state-ocr-delegate-progress.json`. The following IDs follow the order of the 20 sanitized first-run comments.

| ID | Source area | Disposition and evidence |
| --- | --- | --- |
| 1 | apply checkpoint ownership | Fixed. Source/config checkpoint validation now precedes claiming or creating staging output. Rejection preserves both an absent and a pre-existing active plan. |
| 2 | listing timestamp exceptions | Fixed with explicit failed listing progress and a nonzero CLI result. Missing/date-only/invalid values are not skipped or reported as a successful zero-change plan. |
| 3 | listing state error presentation | Deferred low-severity CLI presentation improvement. No synchronization correctness change was required for this note. |
| 4 | legacy state reader UTF-8/error presentation | Deferred low-severity reader hardening. No source content or production state was changed to address it. |
| 5 | pipeline claim before validation | Fixed. Resume/configuration/rebase validation is read-only and precedes run ownership mutation. |
| 6 | observed-only IDs become updates | Fixed. Actual baseline/corpus/source evidence determines existing identity; historical filtering and new-document classification survive page observations and prior attempts. |
| 7 | downloaded corpus marked applied | Not accepted as an applied-version defect. `captured` and `published` have distinct consumers; `get_sync_updates.load_sync_state` only advances from published corpus or established baseline, while graph coverage remains separate. |
| 8 | completed execution leaves active run | Fixed. Completion/journal recovery is validated before persisting a new claim, and retries preserve committed state and graph bytes. |
| 9 | arbitrary filename for v2 state | Fixed. The knowledge state loader rejects v2 files not named `.sync-state.json`, before per-document or final writes. Legacy named state files remain supported. |
| 10 | invalid execute and quoted-home claims | Fixed. Required arguments/module checks precede import or initialization side effects; run identities expand quoted home paths consistently. |
| 11 | legacy import replaces current capture | Fixed. Canonical observation, captured-source, pending and step evidence block destructive legacy import. |
| 12 | explicit legacy read error presentation | Deferred low-severity CLI presentation improvement. |
| 13 | update version option bypasses lock | Fixed. Only the standalone display form `--version` bypasses the decorator; `update --version ...` is locked. The regression holds a real updater lock and passes an invalid upgrade version, with no installation or network operation. |
| 14 | observed/observation naming | Deferred compatibility cleanup. Listing observations and the snapshot observation consumed by knowledge planning remain explicit; no field was silently renamed. |
| 15 | unused load_view helper | Deferred low-severity cleanup. |
| 16 | reinitialization metadata | Fixed through the real CLI. Equivalent trailing-slash URLs are accepted, and an omitted/empty configured name preserves existing metadata. |
| 17 | duplicate record_step helper | Deferred low-severity cleanup. |
| 18 | candidate-only retains active run | Not changed. A retained candidate is an intermediate result before graph publication; the design requires the same run to continue with its staged candidate, rather than admitting a different updating plan. `graph_candidate` completion does not advance graph coverage. |
| 19 | completed deletion remains pending | Fixed. Successful graph deletion removes only `GRAPH_DELETION_REQUIRED`, retaining unrelated review/pending reasons. |
| 20 | metadata forces strict comparison action | Not accepted as the reported knowledge-route failure. The cited `mdcompare.decisions.choose_action` belongs to strict `compare`; `knowledge_plan._compare` calls `compare_knowledge_documents`, which does not consume strict `metadata.status` or that action selector. Title/context rules remain active. |

The additional low-severity `sync_time.py` separator-format suggestion was deferred, along with the six low-severity first-run comments; the accepted timestamp syntax was not broadened during correctness fixes.

Host review also reproduced a phase-boundary defect: an incomplete capture could produce a plan, then a repeated discovery could overwrite the same snapshot/raw and invalidate that plan. Complete snapshots were already rejected at capture entry. The narrow fix rejects `phase=capture` when its matching active run already has `plan_staging`, before listing acquisition or raw/snapshot writes. The real CLI regression first observed a successful overwrite, then passed by proving the original state, snapshot and raw bytes unchanged. This does not add abandonment, automatic replanning or a new workflow engine.

Final post-OCR `make check` passed document guards, lint/format and **1,027 tests in 134.91 seconds**. The affected focused subset passed **84 tests in 20.85 seconds**. Final installed `make smoke` passed from the built wheel/sdist with a separate task-owned updater state directory. These runs cover the final source revision after all accepted OCR findings and the reproduced capture/plan boundary fix; the previous 1,021-test evidence is for commit `6be0957` and does not replace these final gates.

Final logs: `/tmp/dogpdkb-sync-state-review-fix-ocr-final-check.log`, `/tmp/dogpdkb-sync-state-review-fix-ocr-focused.log`, and `/tmp/dogpdkb-sync-state-review-fix-ocr-final-smoke.log`. Documentation guard and diff whitespace checks were repeated after adding the final dispositions. No production actions are included in this closure.

## PR 17 follow-up review corrections

All three follow-up comments were reproduced through the existing CLI boundaries and confirmed valid. A retained comparison now records its original graph/source identity and binds the incoming version, raw reference and metadata. Same-directory retries freeze and validate those original inputs before reusing a completed comparison; changed original, frozen or incoming bytes and changed metadata/version/reference remain rejected. Configured v1 migration initializes matching existing corpus baselines before claiming a run, preserving proven precise times without graph extraction. Reinitialization retains an explicit baseline-source conflict when duplicate resolution removes the registered source; the original surviving source clears the conflict, and independently recorded successful corpus/graph rebinding is not misclassified as a duplicate conflict.

Final follow-up `make check`: **1,039 tests passed in 110.48 seconds**, with document guard and Ruff checks/format passing. Final isolated wheel/sdist `make smoke`: **PASS**. The first smoke invocation was blocked by sandbox write access to the existing uv cache before building; the authorized rerun reused that cache successfully. Logs: `/tmp/pr17-review-fixes-check.log` and `/tmp/pr17-review-fixes-smoke.log`. No production data, graph or co.bot state was changed. These gates supersede the earlier 1,027-test count for the PR follow-up source revision; the earlier review-scope limitations remain unchanged.
