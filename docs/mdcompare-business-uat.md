# mdcompare: business-change and extraction-cost acceptance

Date: 2026-11-07. Rule version: `body-units-v2`. Current worktree is uncommitted.
For the 2026-11-08 `body-units-v3` algorithm comparison and updated candidate
counts, see [update-doc filtering exploration](mdcompare-filter-exploration.md).
This record supersedes treating the earlier 2,835 REVIEW rows, or 2,857 non-REUSE
rows, as a Graphify extraction count. That interpretation was incorrect.

## Diagnosis

The initial 2,835 REVIEW documents included 658 with no added/revised non-heading
body candidate: 552 were held by title context and 106 by old-side-only losses.
Of 29,476 initial added/revised candidate units in paired documents, 18,618 already
had identical kind and normalized wording somewhere in the old body; context
alignment, rather than new wording, caused the candidate.

A stratified diagnostic audit examined 20 real documents without algorithm scores
or the fixed98 weak labels. No new business fact was observed in their incoming
differences: 13 were old-side-only losses, three involved formatting/table remnants,
three indentation, and one encoding repair. This is a selected diagnostic sample,
not an estimate of population accuracy or a full semantic gold set.

## Changes and boundaries

- Normalize supported cosmetic HTML and simple HTML/Markdown table representations.
  Preserve URLs, unknown semantic attributes, cell bindings, code, negation and
  explicit subject/condition scopes. Complex or unsupported tables remain distinct.
- Exclude explicitly bounded generated regions without assessing their content.
  Images and OCR continue to have no judgment, task or prerequisite in this command.
- Remove arbitrary ordinary-paragraph inheritance that cascaded into unrelated
  following rules. Structured code/table/ordered scope and explicit subject frames
  remain protected.
- Keep body comparison separate from a read-only `graphify_plan`. Old-side losses,
  title uncertainty, read failures or exhausted budgets never cause a full-source
  extraction fallback.
- Identical old wording in a different context remains REVIEW, with its old source
  reference. It goes to context review rather than body re-extraction. A changed
  condition is reviewed once; its unchanged child wording is not repeated as new.
- Strictly located rich-HTML/table indentation artifacts remain presentation review,
  not confirmed knowledge reuse. Literal fenced code, Python/XML, changed values
  and preformatted content are not projected away.

## Final real-file results

The same frozen inputs were rerun in a new private directory. Earlier reports and
builds remain retained. Action counts are a plan, not executed model jobs.

| Plan action | Full primary: 4,752 | Alternate old: 16 | Fixed98 |
| --- | ---: | ---: | ---: |
| KEEP_EXISTING | 2,087 | 10 | 6 |
| HOLD_EXISTING | 1,861 | 0 | 25 |
| BODY_DELTA_CANDIDATE | 804 | 6 | 67 |

The primary comparison still has 2,641 REVIEW rows. This is intentional: uncertainty
remains visible while no longer being converted into automatic extraction work.
All runs have zero UNKNOWN body reads and zero item errors, conserve counts and
complete their alignment budgets. They exit 2 because review remains.

The primary plan has 5,200 textually new candidate units across 804 documents,
6,180,342 characters of candidate-unit text. This includes whole candidate tables
or paragraphs; it excludes extra review context. It is neither a minimal patch,
actual new knowledge size, model token count nor a billed cost. It remains too
noisy to certify 804 real business updates. New document IDs likewise do not prove
new knowledge absent elsewhere in the knowledge base.

Of the selected 20 no-observed-new-fact cases, final routing is 14 HOLD, one KEEP
and five remaining candidates containing 3,520 characters. The intermediate
post-normalization plan still had 15 candidates and 38,660 characters. This is
evidence that structural noise drove substantial work, but the remaining false
candidates also show automatic business-update acceptance is not yet established.

## Graphify execution is a separate gate

The current updater selects `manifest.exported` as source scope and uses an
existing file-level semantic cache before model invocation. File input counts
therefore do not establish actual LLM call counts or spend. This run did not audit
production cache hits or invoke Graphify.

The current graph merger replaces records owned by an affected source. Passing
only a changed fragment as though it were a complete source would risk dropping
untouched knowledge. This change does not alter the production selector/merger,
send these candidates to Graphify, or claim that partial extraction is already
compatible. Confirm real fact changes first; source-unit ownership and safe merge
semantics are required before executing fragment-level updates.

Source references: [selection](../src/dogpdkb/workflows/update_graph.py:53),
[file cache](../src/dogpdkb/workflows/update_graph.py:90),
[source ownership replacement](../src/dogpdkb/workflows/graph_sync.py:178).

## Verification and retained evidence

- Full repository gate: 838 tests passed, repository Ruff and document checks pass.
- Focused body tests: 116 passed. Independent cases preserve currency/subject
  changes while separating context and display review from extraction work.
- All 13 synthetic update probes retain a non-reuse comparison item. General
  paraphrases remain unsupported; only the independent-fact reorder probe among
  four broad equivalence probes became complete reuse. Development probes are
  separate from real UAT.
- Final wheel/sdist built offline; isolated installation smoke passed with the
  installed knowledge command. No release or production integration occurred.
- The 9,500 full-capture files and 307 fixed-package files are directly unchanged
  against retained originals; JSON reports are private mode 0600.

Private evidence is under `tmp/mdcompare-knowledge-research-20261107/`:
`business-initial-diagnosis.json`, `business-semantic-audit.json`,
`business-diagnosis-unit.md`, `graphify-routing-findings.md`, and
`business-final-uat/` (reports, summary, sample re-evaluation and verification).
Bodies and production responses are not copied into this document.
