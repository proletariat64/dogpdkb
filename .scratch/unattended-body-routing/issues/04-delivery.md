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

## Installed acceptance and release

- PR #25 latest head f70476e received Greptile 5/5 after fixing a reproduced interrupted-replan cleanup defect. Both ordinary CI jobs passed; isolated co.bot real-file CLI and native Graphify 0.9.96 mock-provider acceptance passed.
- PR #25 merged at 18b317d and v0.2.5 was published to AntCode with verified anonymous installer and stable homepage metadata. Local/co.bot installed CLIs and official production preflight passed.
- Official production resume/replan completed selection but stopped safely before extraction on a deferred new source with no prior graph witness. The public graph remained untouched. Follow-up acceptance is tracked in 05; production completion is still pending.
