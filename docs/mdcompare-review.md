# mdcompare integration review

The full integration was reviewed against the actual default branch `origin/master` (`4da979fec2775f68dec46fb2ab62ec5e062678ed`) at `212b1d991f8fbce86601b9e1b749948ff674ae0d`. Standards and Spec were independent parallel reviews; local OCR used the configured provider on 15 selected code items. The graph was refreshed with source/test coverage checked; subprocess CLI coverage inference gaps were not treated as missing test evidence.

Corrections were committed as `02ea393ab8bece7184bff8ab0b3cf26acddaa0ec` and integrated as `0ca16a88a46df11a2c5137223fcb54a9e4190cc1`. No production responses, ignored corpus files or credentials were included in provider context.

## Original two-axis reports

## Standards

No material code-standard breach or refactor need was found. The new modules use the documented prepared-image/OCR-block vocabulary, retain ignored corpus boundaries, and keep tests at the CLI/installation seams. Tool-enforced formatting is excluded from this review.

- **Low — documented commit convention:** merge commit `6aded08` has subject `Merge branch 'pre-ocr-content-dedup' into mdcompare/ticket03`. The user-provided global AGENTS instructions, “Tools and environment”, require conventional prefixes for commits. This subject lacks one. Use a `chore:` merge subject for future integration merges; reword this local commit before publication only if preserving the existing commit graph and evidence pointers is not preferred. This is metadata-only, not a code defect.
- **Low — possible Middle Man / Speculative Generality (judgement call):** `src/mdcompare/io.py:204–205` adds `def atomic_write(path, content): publish_reports([(path, content)])`. The CLI directly calls `publish_reports`; no source/test caller uses the wrapper, and it survives only as an internal-contract documentation reference. Removing it and naming `publish_reports` in the contract avoids retaining an unused second publication API. No broader refactor is warranted.

Standards: 2 low findings; worst is the commit-subject convention breach. No medium/high/critical Standards finding.

## Spec

1. **High — unrecognized embedded dependencies can authorize a verified skip** (`src/mdcompare/resources.py:148`, `src/mdcompare/resources.py:298`). The spec requires “仅有 URL 时资源状态为未知” and permits `SKIP_VERIFIED` only when “所有相关资源已确认未变”. The HTML extractor uses a closed tag whitelist and the fallback recognizes only CSS `url(...)`. A raw document containing `<input type="image" src="https://example.invalid/image">` or `<style>@import "https://example.invalid/style.css";</style>`, paired byte-identically with `resources: []`, currently exits **0**, reports resources **NOT_APPLICABLE**, and chooses **SKIP_VERIFIED**. Neither external image nor stylesheet has verified bytes/version evidence, so replacement behind the unchanged URL is silently missed. Recognize these forms or conservatively return `RESOURCE_SYNTAX_UNVERIFIED` for unsupported resource-bearing HTML/CSS; add CLI regressions. Both failures were reproduced through the installed CLI in temporary development fixtures.

No additional confirmed missing/partial requirements or unauthorized production behavior were found. The real-98 acceptance remains correctly scoped to legacy enriched-versus-raw inputs; its UNKNOWN results do not establish image equality or successful raw-baseline reuse.

Spec findings: **1 high**; worst issue is an unsafe automatic skip with an unverified external dependency.

## Disposition

- Standards: removed the unused `atomic_write` wrapper and updated the internal contract. The low conventional-prefix deviation in one intermediate merge is retained to preserve the existing commit graph and acceptance pointers; subsequent integration merges use conventional messages. It has no runtime effect.
- Spec: closed the high unsupported-dependency skip path. CLI regressions also cover Unicode fake line starts and literal backticks inside raw HTML blocks; only proven code is excluded from discovery. Unsupported resource-bearing HTML/CSS stays UNKNOWN.
- Resource work: malformed bracket/angle/backtick input is rejected before expensive masking/matching by a documented cumulative syntax-work limit. No persistent hash/index was added.

## Local OCR

Session `1f3780fd-b7d1-4ae4-adac-b33769f8dbe3` completed with exit 0: **15 selected / 15 completed / 0 failed**, no LLM failures. It reported **5 medium and 2 low** findings. All seven code findings were addressed in the same corrective branch:

| Finding | Final behavior |
| --- | --- |
| Missing error-path JSON fields | UNKNOWN dimensions retain the complete documented shape; unverified equality facts use null. |
| Repeated OCR record/resource scans | Lazy per-document maps/indexes replace per-image reconstruction; changed resources bypass obsolete reuse evidence. |
| Unused OCR options parameter | Removed from internal function, caller and contract. |
| Removal hidden by obsolete byte read | Confirmed removal is retained without reading an unnecessary old resource file. |
| Specific manifest diagnostics flattened | Explicit manifest/input reason codes survive broad parse-error handling. |
| Unreachable raw history guard | Removed. |
| CRLF reference dependency missed | Resource extraction supports CRLF and lone CR without altering text comparison bytes. |

The complete initial OCR run is not represented as a second provider review of the corrected head. Post-fix proof consists of independent Spec recheck, focused regression tests and the full final gates below. A sanitized OCR summary is retained with private local evidence; provider reasoning is not copied into this tracked record.

## Independent post-fix recheck

## Spec recheck

Reviewed fixes from `212b1d9` through `0ca16a88a46df11a2c5137223fcb54a9e4190cc1` against the approved mdcompare spec. **No material finding remains in the reviewed fixes.**

- Original high finding closed: installed-CLI reproductions using an image input and CSS `@import`, without resource evidence, now produce `UNKNOWN` and exit 2.
- Raw HTML-block backticks and Unicode fake line breaks no longer hide unverified images; both independent CLI reproductions produce `UNKNOWN`/2.
- A CRLF reference-style attachment with paired local bytes still produces resource `UNCHANGED`, `SKIP_VERIFIED`/0, confirming the conservative masking changes preserve this valid path.
- The former 64,000-unclosed-bracket parser stress input now terminates with `RESOURCE_BUDGET_EXCEEDED`/2 in approximately 0.05 seconds instead of timing out; it is not classified equal.
- Inspected the accompanying lazy OCR indexing, removed-resource handling, manifest diagnostics and complete failure-dimension defaults. These preserve the separate resource/OCR decisions and conservative unknown behavior.

These were focused development-fixture checks, not replacement real-file UAT. No repository files or production inputs were changed; the full suite was not repeated.

Post-fix Spec findings: **0 material findings**. Original high finding: **resolved**.

## Final gates

- 194 focused comparator/distribution tests passed; full repository suite **722 passed in 31.61s**.
- Ruff check/format, doc-guard and all new source/test Pyright checks passed (zero type errors/warnings).
- Final wheel and sdist rebuilt offline; isolated installed mdcompare and existing platform smoke passed. No release was published.
- Final [real-file UAT](mdcompare-uat.md): two identical 98-result runs, exit 2/2, all UNKNOWN, all 307 inputs unchanged. Missing historical raw and resource proof remain explicit; development fixtures do not replace real UAT.

Standards: 2 original low findings, one removed wrapper and one recorded commit-metadata disposition; worst original issue was the prefix convention. Spec: 1 original high unsafe-skip finding, resolved; no material post-fix finding remains.
