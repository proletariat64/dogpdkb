# feat: mdocr help and command-specific OCR contracts

Type: task
Status: resolved
Blocked by: none

## Scope

Implement the spec for mdocr only. Own src/mdocr and tests/test_mdocr_help.py. Add short/long/full local help, independently accurate prepare/ocr/batch contracts, examples, partial and token/cache semantics, and public CLI regressions.

Spec: [CLI help redesign](../spec.md).

## Acceptance

Meet the relevant spec decisions through the agreed public CLI seam. Record red/green evidence, focused checks, delivered commit or patch, and limitations before resolving.

## Comments

## Answer

Merged b2799ae via c5413b5; 123 mdocr regressions passed, including 19 help cases.
