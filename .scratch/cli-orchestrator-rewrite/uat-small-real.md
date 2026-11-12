# Three-document live sync UAT — 2026-11-13

## Result

PASS for this three-document, image-free subscription, through an installed wheel
in a disposable environment. This does not establish full-book production
acceptance, native Table export, image download or image OCR quality.

Source book: global-fund-platform/gom7xx. Subscription key: small-real.
The source document IDs are 585581197, 586878618 and 585582036.
No tool or provider was mocked. No Graphify/Yuque/Skylark source was changed.
Production corpus, graph and state were not written.

## Initial chain

Run `run-20261113-022744-a9aaadb2`:

1. Real Skylark paginated 7,550 documents and selected only the three configured
   IDs; all three real exports completed.
2. Installed mdcompare catalog/knowledge/compare ran on the real Markdown.
3. The run paused after plan; real mdocr batch completed image-free checks and
   apply published three complete Markdown files. No image OCR request was made.
4. After explicit user authorization for the three documents and the existing
   DeepSeek API, `sync --resume` ran the original Graphify extract CLI against
   the isolated corpus, using the real DeepSeek service. Initial automatic
   approval rejection was respected; no model call occurred before authorization.
5. Sync exited 0, all three export/compare/OCR/graph steps are completed, all
   three graph source versions match corpus versions, and pending facts cleared.

Graphify produced 48 nodes / 70 edges from exactly three source files.
Its tool diagnostics mark 20 semantic nodes verification=unverified; this is
retained as a tool quality signal, not treated as an orchestrator fatal error.
Tool-reported usage was 4,016 input / 15,197 output tokens (not billing proof).
No manual knowledge-quality assessment was performed.

## Fresh unchanged sync

Run `run-20261113-023957-59c734f7` refreshed the full live listing, exited 0,
selected zero documents, recorded zero failures and had only a listing log.
There was no export, comparison, OCR or Graphify subprocess log.
Direct byte and nanosecond-mtime comparison confirmed the three Markdown files,
graph.json and native manifest.json were all unchanged. All three source graph
facts stayed completed, with no pending documents.

## Installation and artifacts

Built wheel: `/tmp/dogpdkb-small-subscription-dist/dogpdkb-0.2.0-py3-none-any.whl`.
The imported runtime was under the task venv's site-packages, with PYTHONPATH
removed, rather than a checkout/editable import.

Private local evidence lives under
`/home/ubuntu/dev/dogpdkb/.scratch/small-subscription-uat/`: books.json,
dogpdkb.toml, stdout/stderr captures, run plans/reports, corpus, state,
`before-noop/` and `noop-verification.json`. Actual document bodies, graphs,
credentials and environment packages are excluded from this Git delivery.
The local configuration explicitly selects restored Skylark commit 0e7c218,
installed mdcompare/mdocr and the unmodified user-level Graphify executable.

## Repository validation

`make check`: 817 tests passed in 77.14 seconds, Ruff/205 formatting checks and
109-document guard passed. Focused small-subscription/core checks: 22 passed.
Native Graphify isolated tests and installed-wheel smoke are recorded separately
in acceptance.md; test substitutes there are not presented as this live UAT.
