# Semantic score routing delivery map

- Integration branch: `build`, scoped to independent `mdcompare`. The existing draft PR for `dogpdkb` orchestration remains separate.
- [01 — route policy](issues/01-route-policy.md) defines the public seam and its tests.
- [02 — release](issues/02-release.md) follows the implementation and records installed smoke, hosted review, CI, merge and release evidence.

## Accepted offline recomputation

| Diff operations per document | Prepared | Compile | Skip | Sent unscored | Oversized unsent |
|---:|---:|---:|---:|---:|---:|
| 1–3 | 253 | 133 | 112 | 8 | 0 |
| 4–10 | 202 | 114 | 68 | 19 | 1 |
| 11–20 | 179 | 68 | 106 | 4 | 1 |
| 21–30 | 98 | 19 | 74 | 4 | 1 |
| 31–40 | 80 | 25 | 48 | 7 | 0 |
| 41–50 | 50 | 12 | 31 | 7 | 0 |
| 51–150 | 215 | 62 | 142 | 8 | 3 |
| 151–716 | 81 | 21 | 26 | 4 | 30 |
| **Total** | **1,158** | **454** | **607** | **61** | **36** |

Each document contributes its latest assessment only. The default threshold is exclusive: scores above 3 compile; scores at or below 3 skip. Ambiguity interception defaults to no. The recomputation retained 70 numeric high scores originally rejected only by the ambiguity gate and reclassified 361 score-3 documents. Other rejected or absent scores remain unscored. These aggregate counts are not an independent accuracy estimate and do not establish a safe rule based solely on the operation count. No individual documents, model responses or other corpus artifacts are published here.
