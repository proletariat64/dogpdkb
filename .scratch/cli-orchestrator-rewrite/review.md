# Final implementation review

## Standards review

Frozen range: `9ba4cb1eec42154e821d23966dd183f820d1ae6c...2da50e218148a4f3530c789abbdfa45939d5f4f3`, worktree `/tmp/dogpdkb-cli-orchestrator-integration`.

Counts: **1 documented-standard violation (administrative, P3); 0 judgement-call smells.** No runtime boundary violation found.

- **P3 — Complete the adopted local tracker resolution records.** `.scratch/cli-orchestrator-rewrite/issues/01-tool-cli-contracts.md:3` says `Status: resolved`, but its result remains under `## Comments` at line 23, with no `## Answer`. The same pattern appears in resolved tickets 02–06 and 08. `.scratch/cli-orchestrator-rewrite/map.md:18–24` contains neither result links nor resolved context and still describes the delivered Graphify/Skylark capabilities as unresolved Fog. `docs/agents/issue-tracker.md:30` explicitly requires: “append the answer under an `## Answer` heading” and “append a context pointer (gist + link) to the map's Decisions-so-far.” Add concise answer sections and seven linked result summaries while finishing delivery ticket 07. This finding concerns the map/child workflow already adopted for this effort; it does not require additional runtime machinery.

Reviewed surviving orchestration/configuration/process/state/status code, independent mdocr/mdcompare additions, Web decoupling, and both upstream CLI patch artifacts. CRG metadata matched the exact frozen SHA and worktree; imports and targeted source inspection showed subprocess/public-artifact integrations rather than foreign package imports or copied tool algorithms in dogpdkb. Tool-owned private imports stay in the independent tools. No new orchestrator hash mechanism, automatic cleanup/migration framework, or parallel legacy pipeline was found. The smell baseline yielded no sufficiently consequential additional finding; recommending abstractions merely for dictionary usage or small CLI dispatch repetition would oppose the requested thin implementation.

Read-only review; no code edits, paid service calls, production writes, or additional test executions.

## Spec review — 2da50e2

Two findings; no additional scope creep identified.

1. **[P2] Opaque source versions are still ordered lexically at graph publication.** `src/dogpdkb/graph.py:69–75` rejects `version_key(graph.version) > version`. A successful graph version `v2` followed by a valid newly published corpus version `v10` causes `update-graph` to exit 1 (“newer graph already published”) before invoking Graphify. This blocks an ordinary new version, despite the frozen-fact logic correctly accepting it in apply. Spec `.scratch/cli-orchestrator-rewrite/spec.md:122`: “新来源版本正常处理”; line 103 only requires refusing an actual newer successful version. Use successful facts bound to the manifest/plan rather than lexical ordering. Public CLI reproduction artifacts: `/tmp/spec-review-opaque-g4eyzms2`.

2. **[P2] Frozen inputs can change during earlier OCR work and still be published successfully.** `src/dogpdkb/apply.py:593–600` validates the whole plan once, then processes every OCR input and publishes without rechecking the witnesses. In a two-document public CLI run, while the first external OCR process waited, I edited the second raw Markdown. After releasing OCR, apply exited 0 and published “Changed after frozen plan” while that document’s saved `input_copy` still contained “Frozen second document”, under the originally selected version. Spec line 102: “已有本地计划输入缺失或改变时要求重新生成”; acceptance line 185 requires explicit refusal. Revalidate the relevant raw input before its OCR call and all frozen inputs before any corpus publication. Public CLI reproduction artifacts: `/tmp/spec-review-mutation-ka1qyyth`.

Reviewed fixed-scope/resume, version suppression, baseline selection, independent corpus/graph facts, ordering, graph publication, read-only status and documented external boundaries, using the pinned current graph and source/artifact inspection. Reproductions used temporary real Markdown and external process fixtures only. No paid execution, live gateway, production UAT or upstream real-service correctness was claimed.

## Resolution evidence

- Standards P3: resolved tickets 01–06/08 now include Answer sections and map result pointers; spec/map implementation status updated. Ticket 07 is now resolved with final validation and installed-artifact evidence.
- Spec P2 opaque versions: graph manifest publication retains the previously observed graph fact and uses the same frozen-fact rollback checks as planning. Public CLI regression accepts v2→v10 and rejects an independently advanced v11 graph.
- Spec P2 frozen inputs: each OCR input is checked before and after the external call; the entire plan is checked again before corpus publication. Two public CLI regressions mutate the active/later source while an external OCR process waits; both reject with no corpus publication. Changed in-flight input records an unknown OCR outcome for handoff.
- Automated OCR review completed successfully; detailed dispositions follow below. Final integrated validation follows all confirmed corrections.

## Independent fix verification

The Spec reviewer reran five public CLI cases on the final-fix worktree: opaque v2→v10 acceptance, intervening graph success refusal, old manifest refusal, and active/later input mutation during OCR. All five passed in 1.07 seconds. Both Spec P2 findings are resolved. Detailed read-only verification: `/tmp/dogpdkb-spec-fix-verification.md`.

## Automated OCR review

The configured local `ocr` review completed with exit 0: all 22 selected files
reviewed, zero failed files, 21 high/medium findings. It reviewed the frozen
base-to-2da50e2 code range; follow-up corrections were then independently checked
and tested. One attempted read of a nonexistent filename was a tooling warning;
no review group failed. Corpus, credentials, production responses, deleted legacy
files and upstream patch payloads were excluded from this automated review;
the independent Standards/Spec reviews covered the full rewrite scope.

The raw machine report is `/tmp/dogpdkb-rewrite-ocr-review.json`. The compact
record below preserves every finding and its disposition without copying the
reviewer's internal session logs. None was dismissed as a style preference.

| # | Severity / original path | Finding and correction | Verification / status |
| --- | --- | --- | --- |
| 1 | medium · `scripts/smoke_install.py` | **Timeout descendant cleanup**. Use the existing bounded process helper; snapshot descendant groups before graceful cancellation, terminate remaining observed groups, and reap the parent. | `tests/test_process.py::test_timeout_stops_descendant_in_separate_session` — resolved; focused CLI checks green |
| 2 | high · `src/dogpdkb/apply.py` | **Preserve necessary OCR work when cache is absent**. Pass all current images to mdocr (resources=null); the tool restores verified cache or performs required OCR. New successful contribution forces compile. | `Independent real-tool apply versions 12→13→14: missing record triggers necessary OCR, next valid cache reuses without token; /tmp/dogpdkb-ocr-cache-absence-3un3186_/evidence.json` — resolved; real-tool cache-absence and no-token reuse verified |
| 3 | high · `src/dogpdkb/planning.py` | **Timestamp regression despite frozen facts**. Reject provably older timestamps before frozen-fact acceptance; day precision compares business dates in UTC+8 and permits same-day updates. | `tests/test_orchestrator_apply_cli.py::test_frozen_success_facts_do_not_authorize_timestamp_rollback; test_registered_day_baseline_allows_same_local_day_timestamp` — resolved; focused CLI checks green |
| 4 | medium · `src/dogpdkb/apply.py` | **Historical raw cleanup**. Unavailable historical OCR input falls back safely; missing old files do not abort a fresh valid apply. | `tests/test_orchestrator_apply_cli.py::test_missing_historical_ocr_input_does_not_abort_fresh_apply` — resolved; focused CLI checks green |
| 5 | medium · `src/mdcompare/catalog.py` | **Catalog ID collision**. Reject declared/generated ID collisions rather than silently dropping a resource. | `tests/test_mdcompare_catalog_cli.py::test_catalog_rejects_declared_and_generated_id_collision` — resolved; focused CLI checks green |
| 6 | medium · `src/mdcompare/catalog.py` | **Protocol-relative URL evidence**. Infer local bytes only when both scheme and authority are absent. | `tests/test_mdcompare_catalog_cli.py::test_catalog_does_not_use_local_bytes_for_protocol_relative_remote` — resolved; focused CLI checks green |
| 7 | medium · `src/dogpdkb/graph.py` | **Opaque graph versions; duplicate Spec finding**. Carry frozen previous_graph through manifest and use shared frozen-fact rollback validation. | `tests/test_orchestrator_graph_cli.py::test_opaque_graph_versions_use_frozen_success_facts` — resolved; focused CLI checks green |
| 8 | high · `src/dogpdkb/graph.py` | **Changed same-run graph input**. A graph acknowledgement retains its independent successful input copy, action and metadata; skip only the exact acknowledged input. Graphify reads the frozen input copy. | `tests/test_orchestrator_graph_cli.py::test_changed_input_in_same_run_and_version_is_compiled_again` — resolved; focused CLI checks green |
| 9 | medium · `src/mdcompare/catalog.py` | **Historical catalog inventory**. Preserve source declarations and derive inventory from the tool-verified raw snapshot. | `tests/test_mdcompare_catalog_cli.py::test_catalog_preserves_verified_historical_source_inventory_and_paths` — resolved; focused CLI checks green |
| 10 | medium · `src/mdcompare/catalog.py` | **Moved provenance evidence**. Normalize raw_path/prepared_image/artifact_path against the input manifest; source_reference stays document-relative. | `tests/test_mdcompare_catalog_cli.py::test_catalog_preserves_verified_historical_source_inventory_and_paths` — resolved; focused CLI checks green |
| 11 | high · `src/dogpdkb/sync.py` | **Interruption state write race**. Both sync and apply record interruption facts while holding target writer locks. | `tests/test_orchestrator_operations_cli.py; independent /tmp/dogpdkb-ocr-lock-web-fix-verification.md` — resolved; focused CLI checks green |
| 12 | high · `src/dogpdkb/cli.py` | **Installer deletes active runtime**. Active managed commands share the existing installation lifecycle lock and pass it through tool processes; update/uninstall/installer require exclusive access. | `tests/test_lifecycle.py::test_active_cli_blocks_installer_removal; independent lock/child inheritance verification` — resolved; focused CLI checks green |
| 13 | medium · `src/dogpdkb/workflows/publish_web.py` | **Explicit Web source blocked**. Full explicit source/output/book URL publication bypasses sync-book selection. | `tests/test_publish_web.py::test_explicit_web_sources_do_not_require_enabled_sync_book` — resolved; focused CLI checks green |
| 14 | high · `src/dogpdkb/graph.py` | **Null pending version after graph acknowledgement**. Unknown pending version is a non-match; preserve pending and persist actual graph success. | `tests/test_orchestrator_graph_cli.py::test_unknown_pending_version_does_not_lose_acknowledged_graph_success` — resolved; focused CLI checks green |
| 15 | medium · `src/dogpdkb/graph.py` | **Superseded metadata manifest**. Require manifest metadata to match current published corpus facts before Graphify work. | `tests/test_orchestrator_graph_cli.py::test_superseded_metadata_manifest_is_refused` — resolved; focused CLI checks green |
| 16 | high · `src/dogpdkb/planning.py` | **Foreign URL selectors**. Do not translate mdcompare URLs into mdocr internals; mdocr owns image selection and successful cache reuse. | `tests/test_orchestrator_apply_cli.py::test_image_selection_is_owned_by_mdocr` — resolved; focused CLI checks green |
| 17 | high · `src/dogpdkb/apply.py` | **Pre-plan resume input replacement**. Persist and validate each already-prepared raw input/witness; only genuinely unstarted exports proceed on resume. | `tests/test_orchestrator_apply_cli.py::test_resume_before_plan_refuses_changed_prepared_input; test_resume_before_plan_keeps_original_scope_and_processes_unstarted_source` — resolved; focused CLI checks green |
| 18 | medium · `src/dogpdkb/apply.py` | **Unreliable OCR report promoted on reuse**. Require reliable schema, source count, previous known status and exit code before reusing an old OCR output. | `tests/test_orchestrator_apply_cli.py::test_unreliable_ocr_report_is_never_reused_as_success` — resolved; focused CLI checks green |
| 19 | medium · `src/mdocr/batch.py` | **Relocated image links**. Tool rebases retained plain images and preserved OCR block source/original-image references; text is retained. | `tests/test_mdocr_batch_cli.py::test_retained_relative_images_still_resolve_after_output_moves; test_preserved_ocr_block_keeps_image_reference_when_output_moves` — resolved; focused CLI checks green |
| 20 | high · `src/mdocr/batch.py` | **Discarded callback errors**. Tool records transform errors as partial and classifies malformed provider types as failed resources. | `tests/test_mdocr_batch_cli.py::test_malformed_provider_state_is_reported_as_failure` — resolved; focused CLI checks green |
| 21 | medium · `src/mdocr/batch.py` | **Report overwrites local image**. Tool protects all local image inputs, including unselected and preserved-block references, before writing outputs. | `tests/test_mdocr_batch_cli.py::test_report_cannot_overwrite_unselected_local_image` — resolved; focused CLI checks green |

Changed-area focused gate: 99 tests passed in 31.75 seconds; Ruff and all 205
Python format checks pass. Final complete gate after all automated-review corrections: 811 tests passed
in 67.62 seconds, docguard109, Ruff and 205 format checks green. Rebuilt
installed-wheel smoke passes from `/tmp/dogpdkb-orch-reviewed-dist`; every runtime
Python byte in wheel/sdist matches final source (43 modules). Earlier
789-test/build proof is historical.

Independent real-tool OCR cache verification used public apply/mdcompare/mdocr and
a localhost HTTP provider. With unchanged PNG/body, deleting only the cached
record caused one necessary new OCR request and retained recognized text; the
following version reused the restored cache without an authentication token and
without another request. All three published corpus versions retained OCR, and
the newly generated contribution requested compilation. No paid service was used.

Independent lifecycle/Web/interruption verification passed eight selected cases
and proved concurrent disjoint managed commands, exclusive installer blocking,
inherited child lock lifetime and under-lock interruption writes. Record:
`/tmp/dogpdkb-ocr-lock-web-fix-verification.md`. Independent core verification
passed nine cases (`/tmp/dogpdkb-ocr-core-fix-verification.md`); its day-precision
follow-up is covered by the final public init-state→apply regression. Current
boundary guards report zero foreign imports/helper bypasses.

Final closeout: all Standards/Spec and automated OCR findings are resolved.
Independent day-precision verification reran the original public reproduction and
same-business-day regression successfully. All eight local tickets include Answer
sections and linked map decisions. Final full gate, installed-wheel proof, exact
archive bytes and physical line counts are recorded in [acceptance](acceptance.md).
