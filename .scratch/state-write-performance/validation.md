# Batch state persistence validation

## Before / after measurement

Local Python 3.12, identical synthetic JSON state (34,245,250 bytes), ten real
temporary Markdown documents, actual mdcompare catalog/knowledge/compare CLI
subprocesses, existing fsync/atomic replacement. No network or model calls.
The unrelated synthetic facts remain in the state during every save.

| Metric | master b95838a | batch checkpoints |
| --- | ---: | ---: |
| Comparison wrapper elapsed | 23.417 s | 4.261 s |
| Full-state writes | 21 | 3 |
| Logical state bytes written | 719,248,150 | 102,753,550 |
| Knowledge report results | 10 | 10 |
| Full comparison report results | 10 | 10 |

This single local sample is about 5.5x faster (82% less elapsed time), including
real comparison subprocess startup. It does not measure a production rerun or
promise the same speedup for network/model work. For 3326 active documents,
the comparison state-save count drops from 6653 to three; selected source count
no longer multiplies whole-state persistence at batch boundaries.

## Regression coverage

`tests/test_orchestrator_state_batches.py` counts real atomic state writes at
batch sizes 1 and 25; subprocesses assert that all attempt records are durable
before they execute. It also covers a failed state save, preparation failure,
old-version blocks, actual termination during comparison, interruption cleanup,
public status phases, and Graphify success/failure/interruption batches.

Existing apply/core/graph CLI suites initially passed: 71 tests, plus one live
termination regression passed separately. The final combined targeted run and
review are recorded in the PR. Full make check/make smoke run only in CI.

Production UAT, installed CLIs and state files are outside this worktree and
were not changed.
