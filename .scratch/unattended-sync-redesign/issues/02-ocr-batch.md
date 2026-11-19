# ocr-batch

Status: integrated

Progress: Implemented by `14cd9d8`; 118 focused offline tests passed and 18 affected checks passed after final fixes. Integrated in Draft PR #36; full-scale and integrated UAT pending.

Blocked by: none

Replace per-document Paddle path with one LLM-default batch. Durable exact original witnesses, path-safe mapping, resume/no replay, partial rows, budget, local inputs and per-item failure; own OCR execution/retention and focused tests.

Acceptance: applicable public CLI scenarios in [spec](../spec.md); commit tests and record focused verification.
