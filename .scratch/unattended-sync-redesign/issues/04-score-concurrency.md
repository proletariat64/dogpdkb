# score-concurrency

Status: integrated

Progress: Integrated in Draft PR #36 at `dc2f5f9`; 40 focused offline tests passed.

Blocked by: none

Implement mdcompare semantic-score native bounded concurrency default 3, durable admission/429 drain, unknown suppression, deterministic validated output, independent request budget, unit and CLI test coverage.

Acceptance: applicable public CLI scenarios in [spec](../spec.md); commit tests and record focused verification.

Focused verification (offline, 40 passed):

```bash
tmp=$(mktemp -d)
PYTHONPATH="$PWD/src" ../dogpdkb-release-029/.venv/bin/python -m pytest -q \
  tests/test_mdcompare_semantic_score_cli.py tests/test_mdcompare_semantic_route_cli.py \
  tests/test_mdcompare_help.py --basetemp "$tmp/pytest"
```

Fake Pi SDK coverage includes observed three-request overlap, 429 drain at
all tiers and across an interrupted drain, no replay of admitted requests,
fail-closed behavior with a lost journal, zero/global budget, deterministic
ordering, and invalid provider replies. No live provider or full-suite run.
