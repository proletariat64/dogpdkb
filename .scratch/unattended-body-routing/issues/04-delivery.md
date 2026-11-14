# 04: Review, release and installed production acceptance

Status: claimed
Type: task
Blocked by: 01, 02, 03

## What to build

PR ordinary CI plus latest Greptile 5/5, co.bot isolated selective UAT, merge, publication, local/co.bot installation, official production tmux sync.

## Acceptance

- Prove the corresponding spec behaviors through focused public boundaries.
- Preserve production artifacts and unrelated work.
- Do not run local OCR review, OCR CI or full local regression.

## Progress

- PR #25 opened early as a draft; integrated implementation ready for Greptile.
- Integrated focused regression: 395 passed in 129.28 seconds. Changed Python Ruff checks and format pass; doc guard passes for 150 documents.
- Real frozen v5 replay: 829 candidates, 2,446 HOLD, 51 KEEP across 3,326 inputs; 762 old body-boundary exceptions remain explicit. This is selection evidence, not model execution or cost evidence.
- No local OCR review, OCR CI or full local regression executed. Installed co.bot selective acceptance, latest-head Greptile 5/5, merge/release and production recovery remain pending.
