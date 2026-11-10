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

No production corpus, graph or co.bot state was migrated or updated. No live remote listing, full production mdcompare run, real Graphify model execution, paid extraction or production Web publication was performed. Final integration code review and OCR review are recorded separately by the integrating agent; these checks do not claim those reviews are complete.
