# Authored semantic layer — offline acceptance evidence

Status: implementation and full offline filtering passed; awaiting explicit user filter acceptance. Real compilation, hosted review and release are not authorized/completed by these results.

## Population and input safety

- Original isolated UAT incremental selection: **3,293 = 36 new + 3,257 updated**. Not merely the old compile829/831 cohort, and not a historical backfill.
- Frozen input root: `/tmp/dogpdkb-semantic-layer-uat/`; manifests `baseline.json` (3257), `incoming.json` (3293), explicit `recovery.json` (708 approved document identities).
- Baseline bytes come from original retained `state/.../inputs/<id>/graph-baseline.md`, not mutable corpus paths overwritten by earlier UAT publication. All708 approved repairs reproduce their reviewed recovered copies byte-for-byte using insertion-only projections.
- Final public replay audit verifies **6,550 source/snapshot pairs**, matching captured tool inputs and unchanged originals; **387,371 original-source unit coordinates**. Original repository, production/UAT instance and historical artifacts were not rewritten.
- Public `init-state` registered3257 copied existing corpus baselines without inventing graph success. Declared old versions retain init-state's day precision; incoming versions retain the frozen observed versions. The isolated instance neither imports old runs nor backfills other documents.

## Independent mdcompare preflight

```text
mdcompare semantic --baseline baseline.json --incoming incoming.json \
  --output semantic-report.json --diff-output semantic-diff.json \
  --recovery recovery.json
```

| Semantic route | Count |
| --- | ---: |
| SKIP / EQUIVALENT | 1,968 |
| COMPILE / NEW | 36 |
| COMPILE / CHANGED | 1,256 |
| COMPILE / UNCERTAIN | 6 |
| DEFER / UNAVAILABLE | 27 |
| Total | 3,293 |

COMPILE total **1,298**. Recovered708 split **530 SKIP /178 COMPILE**, none unavailable. Final26 remain DEFER with `BODY_SCOPE_BOUNDARY_UNCLOSED`, without further rescue. Separate document `79545768` has `BODY_SCOPE_BOUNDARY_INVALID` and is the additional defer. All14 withdrawn unsafe/unproven skip proposals compile.

| Old all-increment plan | New SKIP | New COMPILE | New DEFER |
| --- | ---: | ---: | ---: |
| retain1645 | 1389 | 256 | 0 |
| compile831 | 3 | 828 | 0 |
| defer771 | 530 | 214 | 27 |
| reuse46 | 46 | 0 | 0 |

The larger compile queue deliberately advances previously held/deferred authored updates; it is not a reduction from831. Previous all-CHANGED and compile829 experiments have different denominators.

## Public dogpdkb whole-cohort replay

Fresh isolated instance: `/tmp/dogpdkb-semantic-layer-uat/orchestrator/`.

```text
dogpdkb apply --config INSTANCE/dogpdkb.toml --book fund-platform-uat \
  --input INSTANCE/differences.json --stop-after plan --concurrency 3
```

**Exit0, paused at plan:** all3293 identities accounted for, **compile1298 /reuse1968 /defer27**, matching every independent preflight route. All36 new documents compile. No compile manifest/corpus publication or successful graph witness is fabricated by plan success.

- Public Skylark-protocol stub copied frozen exports3293 times; OCR/Graphify stub modes explicitly reject calls. Tool-call log contains exports only.
- Reports/indexes/full sidecars are under `state/fund-platform-uat/semantic/<run>/`, outside wash. All3293 operation graphs have one-to-one occurrence references. Full orchestrator evidence totals **337,798,107 bytes**; the independent preflight generation totals336,820,731 bytes.
- Current final artifact validator independently accepted the complete cohort, including identities/status/extraction/source bindings, missing/duplicate reference rejection and frozen dependency coverage. The merged integration CLI also resumed the complete saved plan with `--stop-after plan`: exit0, unchanged routes, no additional export/OCR/Graphify calls. Full audit passed again afterward.
- All3257 seeded corpus baselines remain byte-identical; no OCR/graph steps were created and no successful graph version advanced. Graphify's existing whole-Markdown input projection was not changed.
- Initial public orchestration took **2,154 seconds**, including3293 isolated exports and existing per-document durable checkpoints. This is distinct from the109-second independent semantic comparison measurement.

### Machine-local evidence

Under `/tmp/dogpdkb-semantic-layer-uat/orchestrator/`:

- `filter-audit.json`: complete coverage, route parity, source/coordinate/reference invariants and zero external-processing calls.
- `compile-candidates.json`:1298 exact planned candidate identities/versions/paths, explicitly **plan-only**, not publication or graph success.
- `apply.stdout`, `apply.stderr`, `apply.exit`: public invocation evidence.
- `resume-plan.stdout`, `resume-plan.stderr`: final merged CLI saved-plan verification.
- `wash/fund-platform-uat/run-20261117-113241-607bb4d1/plan.json`: full3293-document plan.
- `state/fund-platform-uat/semantic/run-20261117-113241-607bb4d1/comparison-9oemzexh/semantic-report.json` and `semantic-diff.json`, with the referenced private evidence directory.
- Frozen recovery manifest/source witnesses under that run's `semantic/.../recovery/`.

Original preflight `filter-audit.json`/`filter-candidates.json` remain at the parent root and are explicitly labelled preflight. Preserve indexed evidence directories with their indexes. Corpus text and machine artifacts are not uploaded to Git/PR.

## Corrections and regression gates

- Initial one-shot full-diff JSON materialization was killed by OOM and produced no accepted result. Replaced with per-document streaming and canonical references; measured full semantic replay109 seconds, peak RSS86,180KB. Subsequent reruns retained identical routing.
- Full coordinate audit found a repaired EOF footer extending past the original last line; CLI red/green regression fixed original-coordinate mapping (`04c9990`). A context move stealing an occurrence reserved for revision also received a failing public regression and one-to-one ownership fix (`47d121f`).
- Integration rejects malformed authoritative output before publication, records launched unusable comparisons as unknown, and never invents equality from absent diagnostic reports. Explicit frozen recovery validation also rejects removal of dependency entries, not merely changed source bytes.
- **1,202 regression tests passed** across all tools/orchestration. All32 focused semantic CLI cases and14 new orchestrator semantic integration cases are included. An additional10-case resource-publication check passed after making an existing regex expectation explicit for static analysis.
- Ruff check/format and doc guard passed. Isolated wheel/sdist installation smoke passed, including **91 offline help/version checks** with no writes/network/subprocess activity during help.
- Host-only test detail: native Graphify protocol tests isolate HOME, which hides the unchanged user-installed package. A temporary PATH launcher preserves its package search path for its own subprocesses; tests contact only their local HTTP fixture. No Graphify source/provider was changed, and these tests are not a real-model compilation smoke.
- Active LSP probes reported no new actionable errors for six changed implementation/test files, but did not confirm clean; this is not claimed as a clean LSP verdict. The unchanged parser's reported missing namespace parameter is present and forwarded, recorded as false-positive; Ruff/tests/install smoke provide the confirmed gates.

## Pending delivery gates

1. **Explicit user acceptance of the complete filter results. Stop here before real compilation.**
2. Agree20-plus representative candidates and provider/model/auth/billing, estimated token/cost allowance and hard cap; then real Graphify/patched OpenAI-OAuth smoke.
3. Hosted `@greptileai review pr`, genuine findings fixed through actual confidence5/5; no score manufactured.
4. Repeat real smoke, then merge and release. Draft PR remains unmerged until all gates pass.

Real Graphify requests0; real OCR requests0; document-classification model requests0. Implementation assistance uses the configured pi model separately, not corpus-classification API experiments. No /tmp cleanup was performed; historical repair/experiment evidence remains available for separate archival.
