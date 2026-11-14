# Unattended body routing

Status: ready-for-agent

## Problem Statement

Routine KB sync treats comparison uncertainty as permission to re-extract whole documents. Exporter metadata is compared as knowledge, malformed historical OCR boundaries contaminate unrelated comparisons, and the compiler scans beyond the intended body candidate set. Operators cannot leave the daily job unattended without excessive extraction cost or manual gates.

## Solution

Recognize verified exporter metadata as non-body, isolate source-scope failures, and automatically retain existing graph knowledge for complete comparisons with no new body candidate. Route concrete meaningful changes to complete-source updates. Defer unreadable or incomplete comparisons durably without blocking healthy documents, inventing success or retrying the same uncertain version. Ensure native Graphify scope actually respects selected work while preserving existing graph records.

## User Stories

1. As an operator, I want exported IDs and word counts excluded from knowledge so formatting does not trigger extraction.
2. As an operator, I want both old and new exports treated symmetrically.
3. As an operator, I want malformed legacy generated regions isolated to their source.
4. As an operator, I want readable paired documents compared even if an unrelated baseline fails.
5. As an operator, I want no-candidate comparisons to preserve existing graph knowledge automatically.
6. As an operator, I want unresolved input failures recorded without asking for manual approval during the job.
7. As an operator, I want real subject, condition, amount, table, order and code changes preserved as candidates.
8. As an operator, I want no invented successful extraction for retained or deferred documents.
9. As an operator, I want actual extraction limited to selected complete sources without losing unselected historical graph knowledge.
10. As an operator, I want existing public CLI boundaries, locks, input witnesses and same-version suppression preserved.
11. As an operator, I want selective real-file acceptance in isolated co.bot paths with a mock Graphify process before publication.
12. As an operator, I want a reviewed release installed on local and co.bot before resuming normal production UAT.

## Implementation Decisions

- Keep current semantic-change protections; do not roll back to the historical algorithm. Historical 681 candidates are diagnostic, not an acceptance target.
- Exclude only verified complete exporter metadata at its known header location with matching identity. Preserve document title, unrelated text, literals and malformed lookalikes. Support symmetric historical/current inputs without hardcoding production book names or IDs.
- Preserve strict unknown-tail handling. Do not discard unknown body tails or infer generated provenance from arbitrary labels.
- Narrow incomplete baseline uncertainty to decisions that actually depend on that missing evidence; do not downgrade local new-body candidates merely because another document in the book failed.
- Retain existing public graphify_plan actions and expose enough machine-readable evidence to distinguish complete no-candidate HOLD from unavailable comparison. Meaningful subject/context changes must not be silently retained.
- Orchestrator consumes public reports only. Complete no-candidate HOLD retains old graph without falsely certifying equality; unavailable comparison becomes a durable per-document exception. Concrete candidates use full-source updates, never fragment patches.
- Corpus and graph facts remain separate. New raw/corpus publication does not itself certify graph extraction. Metadata/resource/OCR behavior remains explicit and must not silently override body failures into expensive work.
- Native Graphify extraction and merge stay inside the unmodified external CLI. Do not edit native cache identities, fabricate successful manifests, rewrite graph records, or introduce a fork. Verify how selection affects native scanning and preservation before implementation.
- Existing pending/failure/suppression mechanisms carry deferred outcomes; no new retry engine, migration layer, SQLite, semantic model stage or fingerprint mechanism.
- Saved obsolete plans are not silently rewritten or trusted. Production recovery must use official commands and validate identity, version, targets and witnesses.
- Documentation and code remain English. No local OCR review or OCR CI. Greptile is the AI reviewer.

## Testing Decisions

Use public mdcompare and dogpdkb CLI/report seams, existing subprocess fakes and real frozen Markdown. Focused regression covers exporter-header lookalikes and literals, unreadable baseline isolation, truthful retained/deferred state, meaningful semantic changes, untouched source preservation, same-version suppression and native selection. No local full make check or make smoke. Ordinary PR CI runs full gates. After latest Greptile 5/5, run selective isolated co.bot CLI UAT with real Markdown and mock Graphify compile. Mock acceptance does not prove paid extraction. Only then merge, release, install both hosts and continue production UAT through official dogpdkb in co.bot tmux.

## Out of Scope

Full corpus rebuilding, automatic historical backfill, foreign tool source changes, fragment graph merging, arbitrary strictness thresholds, new model providers or increasing DeepSeek output quotas, human-review queues, web publication, credential changes and unrelated cleanup.

## Further Notes

Read-only investigation and replay are retained in the original workspace under .scratch/hold-routing-assessment. Full real replay: 3,326 incoming / 3,291 old snapshots. Historical symmetric metadata removal selected 681 candidates, including 243 with current baseline-boundary failures; this is not proof of safe production execution. Production Graphify remains stopped until the requested review, acceptance and release gates pass.
