# Independent tool CLI contracts

These are generic tool interfaces. Book/version selection, plan decisions, agent
handoff, scheduling, and publication belong to the caller.

## mdocr plan and run

The active interface is `mdocr plan --mode paddle|llm --input FILE --output FILE --plan PLAN`,
then `mdocr run --plan PLAN --result RESULT`. For batch inputs, use `--list` (JSON array
of Markdown paths resolved from the list) or `--directory` (recursive Markdown paths)
with `--output-dir`. See `mdocr plan --help --full` and [OCR runtime prerequisites](mdocr-runtime.md).
The orchestrator submits one batch plan using `--list`, with LLM as the default and Paddle
only when configured. It binds the result to the plan SHA-256, the planned source and
destination, and the SHA-256 of output/evidence. It does not import the OCR implementation.
`--selection` is a separate optional map keyed by absolute input path; selected image hashes
are bound to each occurrence. Planning performs no OCR. `--download` allows selected
remote images to be downloaded; `--download-list` optionally limits that permission to a
JSON array of absolute inputs, so a mixed batch does not fetch live directory-source images.

`mdocr-result-2` contains `plan_sha256`, `status`, request/reservation state,
concurrency transitions and ordered document rows. Complete rows have `output`,
`output_sha256`, and `mdocr-ocr-2` evidence containing effective mode configuration,
resource IDs, and original image and OCR-block paths/byte digests. LLM evidence also
binds the **document evidence**, not provider context, with the SHA-256 of
`[document_sha256, [[occurrence_id, image_sha256], ...]]`, using plan occurrence order
and selected images only. The serialization is UTF-8 JSON with two-space indentation,
no ASCII escaping, and a trailing LF. New LLM plans declare
`settings.llm_schema: "mdocr-image-content-1"`, which must match evidence configuration.
Legacy document-context plans cannot be executed; existing terminal reports remain
verifiable against their legacy plan and schema. Do not create replacement plans to
replay previously unknown admissions.
A failed `svg_profile_cleanup_failed` occurrence can carry `send_state: "not_sent"`
only when every admission for the image key failed before a provider request.
Admissions carry `source_sha256` so the orchestrator can check the claim against
all attempts for the frozen image in the sealed native journal. Same-version
legacy admissions bind through the separate `legacy_request_sources` map
derived from validated job keys without rewriting attempts. Missing or
contradictory history is not proof of no send. A cache hit alone is not a send.

The orchestrator passes `[ocr].concurrency` to `mdocr plan --concurrency`
(1-100, default 3). mdocr may lower the active limit after throttling.
`[ocr].fix` passes `mdocr plan --fix N`. The installed sample sets `1`;
missing or `0` disables it. The batch freezes this limit. After the initial
pass the same journal records retries as `fix_round`, bounded per image.
Only an observed connection failure before headers or during an SSE body read
(`connection_outcome_unknown`) may be retried despite an unknown result; the previous outcome remains unknown and
the retry may incur duplicate usage. Other unknowns are not replayed. Fixes do
not override request/deadline/throttle stops. Mermaid repairs remain separate.

Failed, held or unknown rows cannot claim a completed Markdown output. The plan-side
journal prevents resubmission if result mirroring was interrupted. LLM admissions and
the global request budget are per image; concurrency limits active Markdown pipelines,
with one image at a time per document. Exit 0 means complete, 2 incomplete, and 1 fatal
invocation or artifact error.

## mdcompare catalog

```
mdcompare catalog --input descriptors.json --output manifest.json
```

Input is the existing mdcompare manifest schema (`schema_version: "1.0"`,
`documents` with `knowledge_base`, `document_id`, `path`, `content_kind`, and
`metadata`); `resources` may be omitted. Output is a full mdcompare manifest with
absolute document/resource paths and a `resources` list populated from Markdown
uses. Entries contain `resource_id` (target), `url`, `kind` (`image`, `attachment`
or `media`), and `path` when a local resource exists. Existing declared resource
evidence is preserved by target. Remote resources are never fetched. A document's
`resource_warnings` records parser uncertainty; unresolved syntax is not silently
claimed complete. Output must not replace an input document or the input manifest.
Exit 0: catalog written; exit 1: invalid/unreadable input or publication failure.

## mdcompare semantic

```text
mdcompare semantic --baseline OLD.json --incoming NEW.json \
  --output semantic-report.json --diff-output semantic-diff.json \
  [--recovery body-recovery.json]
```

Inputs are the existing descriptor schema1.0, paired by knowledge_base/document_id.
Optional descriptor provenance.version records the actual source version; it does
not establish equality. Filtering is offline: no model, login, image/OCR request
or compilation. New documents do not depend on unrelated baseline failures.

The semantic-1.0 report has scope authored_body and results with identity,
semantic_status, action, reason_codes, extraction.available and
comparison_complete. Legal routes are NEW/CHANGED/UNCERTAIN→COMPILE,
EQUIVALENT→SKIP, UNAVAILABLE→DEFER. Only paired contextual normalized sequence
equality proves EQUIVALENT. Changes include authored deletions, context/order,
conditions, numbers, tables and code. Ambiguous/incomplete alignment with usable
bodies compiles. Missing/unreadable/unscoped bodies defer individually. A reliable
empty body is explicit NO_TEXT evidence, not successful graph extraction.
Baseline-only identities are advisory and never remote document deletion.

Images/alt text and explicitly scoped generated OCR are excluded. Resource/OCR
unknowns are advisory and never veto body routing. Existing knowledge and strict
compare reports remain diagnostics, not substitutes for this routing contract.

mdcompare-semantic-diff-1 indexes all expected documents through relative evidence
paths under its private evidence_directory. Preserve the directory with the index.
Each sidecar contains full old_units/new_units, input paths/metadata/versions,
context/original line coordinates and changes. Operations add/delete/modify/move/
equal/uncertain use zero-based old_ref/new_ref, null for an absent side. Full
machine evidence is never clipped by a human excerpt limit. Per-document streaming
and canonical references avoid corpus-sized JSON memory materialization. Unique
evidence generations keep previous indexes valid; obsolete standalone generations
require explicit cleanup, not silent history deletion.

Optional mdcompare-body-recovery-1 has documents with knowledge_base, document_id,
side (baseline/incoming), source_copy and ordered insertions[{byte_offset,text}].
source_copy resolves from the recovery JSON and must equal original source bytes.
Offsets address original UTF-8 bytes. Only marker/newline insertions are accepted;
no arbitrary content replacement/removal or automatic heuristic repair. Projection
is in memory. Provenance retains original offsets/source_line; units retain
original start_line/end_line and repaired projected_start_line/projected_end_line.
A repair can establish availability but never automatically establishes equality.
Invalid whole schema is error1; invalid per-document replay/witness defers that
document. Runtime snapshot drift refuses stale equality/publication.

Exit0: complete routing including uncertain compilation; exit2: complete report
with DEFER; exit1: invalid inputs/arguments or global publication failure. Outputs
cannot alias inputs, manifests or declared repair witnesses. Requested private
reports are staged together; caught ordinary failures roll back replacements.
A failed invocation must not make an old report appear newly complete. Full
schema/example is installed in `mdcompare semantic --help --full`.

By default `apply`/`sync` scores eligible `CHANGED` documents. The optional
`[semantic_score].request_budget` caps admissions; `0` disables scoring. With
no cap, the admission bound is the number of eligible, reserved documents.
For this work, the orchestrator calls public
`mdcompare semantic-score --report … --diff-index … --output … --request-budget N`
(default `openai/gpt-6.1-terra`, Pi OAuth only), then public `mdcompare
semantic-route`. A single scorer invocation owns concurrency (default 3);
its separate request budget counts durable admissions across resumes. A 429
drains the active tier, persists a lower limit, or stops sending at limit 1.
Keep the score output with its private `.execution.json` journal and `.lock`
file. Exit 2 from the scorer is a published partial result, not a reason to
skip unscored changes. Neither scorer nor router rewrites the input
comparison. The plan binds the report, index, scores and route by SHA-256.
A resume with a frozen plan does not make more scoring calls. Before invoking
the scorer, dogpdkb durably reserves each candidate version in book state and
limits admission to those IDs with repeatable `--document-id` options. A bound
score result distinguishes admitted rows (with a status) from proven unsent
ones (without one). Completed, failed or uncertain admissions do not get a new
score request on a later run for that version; unscored evidence leaves the
deterministic route intact. An interruption before the plan suppresses a second
scoring pass in that same run. Without a verified result, reserved versions
remain suppressed rather than assuming work was unsent.
See `mdcompare semantic-score --help --full` for admission limits and privacy.


## Existing mdcompare reports

`mdcompare knowledge --baseline OLD --incoming NEW --output REPORT` compares body
knowledge only and ignores images/OCR. `mdcompare compare` with the same arguments
compares full resources and declared OCR evidence. Both accept optional `--report`
for human Markdown. Exit 0 is a resolved report, 2 is an unresolved report, and 1
is an invalid invocation/input. Reports, not exit 2 alone, carry per-document
classification and reason codes. See the tool's schema for complete reports.
