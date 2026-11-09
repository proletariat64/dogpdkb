# mdcompare body knowledge: real UAT

This is the initial `body-units-v1` record. See the later
[business-change and cost acceptance](mdcompare-business-uat.md) for the correction
to treating REVIEW as Graphify work and the `body-units-v2` results.

Date: 2026-11-07. Runtime: current uncommitted worktree after `50cb56e`.
This is a read-only report of body reuse and increments. No knowledge was merged,
deleted, extracted or published, and no production baseline was advanced.

## Captured scope

The frozen co.bot capture supplies 4,752 incoming Markdown files, 4,730 matching
document IDs and 4,746 old/new candidate pairs. The primary run covers all incoming
IDs, including 22 without captured old documents. A separate 16-row run compares
every alternate old candidate for duplicate IDs. The fixed 98-pair package runs
separately; it overlaps the larger capture and is not an independent holdout.
Old inputs are honestly declared historical enriched; incoming inputs are raw.

All runs use `mdcompare knowledge` with existing captured manifests and default
budgets. Images and OCR do not participate in decisions and produce no pending
tasks. Missing old raw does not impose a document-wide UNKNOWN.

## Observed results

| Run | Incoming rows | REUSE | DELTA | REVIEW | UNKNOWN | Item errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full primary | 4,752 | 1,895 | 22 | 2,835 | 0 | 0 |
| Alternate old candidates | 16 | 9 | 0 | 7 | 0 | 0 |
| Fixed98 | 98 | 0 | 0 | 98 | 0 | 0 |

All three runs exit 2 because review items remain. Every incoming row and reported
unit is accounted for; every alignment completes within the default work budget.
REUSE is normalized observed-body reuse under the captured context, not a claim
of general semantic equivalence or permission to delete the source file.

The primary run has 3,331 documents with reusable units, including 1,436 with
partial reuse alongside increments or review items. Fixed98 has 93 documents with
partial reuse despite every document retaining some review work. This permits
reuse to remain visible when only part of a document needs interpretation.

The following excludes heading units to avoid inflating knowledge reuse with
titles. Paragraphs, bullets, ordered sequences, tables and code can each contain
multiple facts; these are block counts, not an independently labeled fact count.

| Non-heading unit kind | Full primary | Alternate | Fixed98 |
| --- | ---: | ---: | ---: |
| reuse | 116,420 | 421 | 6,170 |
| add | 26,420 | 6 | 1,709 |
| revise candidate | 5,276 | 11 | 295 |
| old_only_review | 82,520 | 22 | 3,954 |
| unresolved | 60,147 | 0 | 1 |

The primary report's all-unit counts are reuse 153,811; add 28,733; revise 5,281;
old_only_review 90,621; unresolved 80,658. Counts include structural headings.

## What remains unproven

The prior manifest adapter derives names and paths from export filenames. They
are not certified historical remote titles. In 1,338 primary rows, name differences
trigger subject-context review for 80,610 otherwise matched units, including
headings. Another 48 units have ambiguous alignment. These are uncertainty about
context, not confirmed business changes. Inputs were not rewritten to manufacture
matching metadata or raise the reuse count.

There were zero cross-document reused units in these real runs under the narrow
name/path/context contract. Development tests prove the implemented cross-ID and
incoming-batch paths, but this UAT does not establish useful broad cross-document
semantic recall. The capture also omits 51 baseline-only corpus IDs.

Unmarked historical OCR text cannot be identified reliably; the report describes
the available body without judging OCR quality. The extractor supports a bounded
Markdown subset. Implicit business context and general paraphrases remain limits.
No semantic embedding or inference model runs in the shipped command.

The 18 synthetic development probes remain separate from UAT. All 13 update
probes retain a non-reuse change/review item while reusing unchanged background.
Only one of four general equivalent-expression probes becomes full-body reuse;
the others retain review work. The marked excluded-content probe becomes body
reuse. Simple quantity-unit equivalence has additional focused regression proof;
this does not establish unrestricted paraphrase recognition.

## Verification

- `make check`: 790 tests passed; repository Ruff and document checks passed.
- After adding `unit_type`, all 68 knowledge tests passed again. The 13 reported
  adversarial scope/image/link cases passed independent recheck.
- The code graph was refreshed and changes analyzed against `origin/master`.
  Its index omits the new untracked modules even after a full rebuild; their
  validation is direct source inspection and executable tests, not graph coverage.
- Offline wheel/sdist build passed. Isolated installation smoke passed, including
  the installed knowledge command reusing quantity-equivalent body, retaining an
  addition and ignoring missing image paths. This is installation proof, not real
  corpus proof or release publication.
- Two real runs produced identical decisions. The second adds only the
  `unit_type` field; removing it yields byte-content-equivalent parsed reports.
- The full archive's 9,500 source files and fixed package's 307 source files are
  directly byte-identical to retained originals. Reports use mode 0600.

Private inputs, manifests and reports remain ignored under
`tmp/mdcompare-uat-all-20261107-130000/`, `tmp/mdcompare-uat-20261107/` and
`tmp/mdcompare-knowledge-research-20261107/implementation-uat/`.
The latter retains the runner summary, development probe results, preservation
verification and both report versions. No private body content is copied here.
