# mdcompare: update-doc filtering exploration

Date: 2026-11-08. Rule version: `body-units-v3`. The comparison remains offline,
read-only and body-only. Images and OCR do not participate. No Graphify job,
production selector, merger or baseline was run or changed.

## Compared algorithms

All new real-file runs reuse the same frozen full-primary and fixed98 inputs.
Candidate counts are planning output, not confirmed business changes or actual
model cost.

| Algorithm | Full candidate docs | Full candidate units | Full candidate chars | Fixed98 candidate docs |
| --- | ---: | ---: | ---: | ---: |
| Previous structural units (`v2`) | 804 | 5,200 | 6,180,342 | 67 |
| Ordered sentence-prefix residual | 802 | 5,198 | 6,179,653 whole units | Not run |
| Rich/empty-layout normalization (`v3`) | **792** | **5,093** | **6,062,549** | **65** |

The sentence-prefix prototype also reduced partial residual payload from
6,180,342 to 6,154,336 characters, but its two-document gain and low recall do
not justify integration. It remains an isolated experiment.

The integrated v3 algorithm normalizes only whitelisted presentation-only HTML
wrappers and wholly empty table body rows, and gives strict rich-layout
pseudo-code a normalized planning match to its plain/table counterpart. It keeps
all nonempty table rows, column positions, cell bindings, unknown semantic
attributes, URLs, literal code and changed values. Presentation matching does not
promote a unit to knowledge reuse.

A separate blinded host-model audit examined 30 fixed, stratified local candidate
units: 16 were judged locally equivalent and 14 retained real updates. This is a
small qualitative sample, not population accuracy or permission for automatic
semantic filtering. It supports evaluating a fixed-model semantic review layer
after structural normalization; it does not establish a production provider or a
safe partial-source merge.

## Safety and verification

- v3 moves 12 full-primary documents from `BODY_DELTA_CANDIDATE` to
  `HOLD_EXISTING`; no document moves in the opposite direction.
- Fixed98 weak-label candidates are S 26→26, D 25→25 and U 16→14. These are
  safety references, not action gold or a precision estimate.
- All 13 synthetic knowledge-update probes retain their previous outcomes. New
  counterexamples cover nonempty table bindings, empty cells in nonempty rows,
  unknown HTML attributes, hidden content, empty rich wrappers and literal code.
- The remaining candidate set still needs factual or semantic review. The 792
  count is not a certified number of business updates, Graphify calls or tokens.

Private reproducibility artifacts are under
`tmp/mdcompare-knowledge-research-20261107/filter-exploration-20261108/`:
candidate forensics, the residual implementation/tests, semantic audit, final
UAT reports and machine-readable comparisons. They contain no published corpus
bodies and remain gitignored.

## 正式编码续作入口

后续过滤应直接从当前 `body-units-v3` 和 792 篇候选继续，不从零重写，也不恢复整篇相似度阈值判等：

- 当前抽取/对齐：`src/mdcompare/knowledge_units.py`。
- 当前配对、上下文证据与候选计划：`src/mdcompare/knowledge.py`。
- 回归反例：`tests/test_mdcompare_knowledge*.py`。
- 冻结 UAT 复跑：`scripts/mdcompare_knowledge_uat.py`，必须用新的私有输出目录。
- 报告同口径比较：`scripts/mdcompare_compare_knowledge_reports.py`。
- 792 篇字符统计：`scripts/mdcompare_candidate_stats.py`；结果见 [字符总量与分布](mdcompare-v3-character-stats.md)。
- 三模型盲测采样与比较：`scripts/mdcompare_semantic_sample.py`、`scripts/mdcompare_model_comparison.py`；结果见 [三组模型正文判等比较](mdcompare-model-comparison.md)。
- 当前正文范围契约：`.scratch/pre-ocr-content-dedup/knowledge-spec.md`。

新算法先作为只读候选层在 792 篇上评估，保留真实事实变化和零反向新增候选检查；通过后才修改正式 CLI。现有 Graphify 整来源替换边界不变。

See [body-knowledge command contract](mdcompare-knowledge.md) for output semantics
and [business/cost acceptance](mdcompare-business-uat.md) for the still-open
production boundary.
