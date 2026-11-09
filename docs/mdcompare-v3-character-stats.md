# mdcompare v3：792 篇候选的字符统计

日期：2026-11-08。统计对象固定为 `body-units-v3` 的 792 篇
`BODY_DELTA_CANDIDATE`。输入使用既有冻结快照，不运行 Graphify，不读取或比较图片/OCR。

## 口径

- `original_raw` / `updated_raw`：Python 解码后的原始 Markdown 字符数。
- `*_cleaned_body`：使用当前正文抽取器排除图片及明确标记的 OCR 区块后，累加所有抽取单元的源文本；保留剩余 Markdown/HTML 语法和标题。
- `*_cleaned_non_heading`：同上，但不计标题单元。
- 792 篇中 770 篇有 original 对照，22 篇没有；original 统计不以 0 补齐。
- 候选块字符仅累加 `candidate_unit_indexes` 指向的新侧完整单元，不等于整篇正文、最小事实差异、token 或账单。

## 总量与分布位置

分位数使用 nearest-rank；字符数均为 Unicode 字符数，不是 UTF-8 字节数。

| 指标 | Docs | 总字符 | 平均 | P25 | P50 | P75 | P90 | P95 | P99 | 最大 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| original 原文 | 770 | 30,006,276 | 38,969.2 | 10,791 | 20,911 | 41,223 | 85,298 | 124,573 | 240,818 | 1,443,472 |
| updated 原文 | 792 | 22,886,453 | 28,897.0 | 7,548 | 14,129 | 28,848 | 59,402 | 86,690 | 220,535 | 1,265,591 |
| **original 清洗后正文** | **770** | **25,522,036** | **33,145.5** | 8,726 | 17,210 | 34,327 | 71,466 | 102,195 | 216,591 | 1,380,575 |
| **updated 清洗后正文** | **792** | **20,422,218** | **25,785.6** | 6,121 | 11,994 | 24,058 | 52,010 | 77,108 | 216,339 | 1,265,308 |
| original 清洗后非标题 | 770 | 25,217,066 | 32,749.4 | 8,507 | 16,804 | 33,878 | 71,109 | 101,192 | 213,233 | 1,380,525 |
| updated 清洗后非标题 | 792 | 20,158,662 | 25,452.9 | 5,854 | 11,695 | 23,703 | 51,420 | 75,578 | 212,981 | 1,265,258 |

792 篇的**候选块完整文本**合计 **6,062,549 字符**，占 updated 清洗后整篇正文的约 **29.69%**。这个比例只描述候选单元载荷，不代表实际 Graphify 输入或费用比例。

## 清洗后整篇正文的字符区间

| 字符区间 | original docs（770） | 占比 | updated docs（792） | 占比 |
| --- | ---: | ---: | ---: | ---: |
| 0–499 | 2 | 0.26% | 3 | 0.38% |
| 500–999 | 7 | 0.91% | 12 | 1.52% |
| 1,000–1,999 | 11 | 1.43% | 22 | 2.78% |
| 2,000–4,999 | 64 | 8.31% | 113 | 14.27% |
| 5,000–9,999 | 151 | 19.61% | 191 | 24.12% |
| 10,000–19,999 | 194 | 25.19% | 198 | 25.00% |
| 20,000–49,999 | 213 | 27.66% | 167 | 21.09% |
| 50,000–99,999 | 87 | 11.30% | 61 | 7.70% |
| 100,000–199,999 | 30 | 3.90% | 14 | 1.77% |
| ≥200,000 | 11 | 1.43% | 11 | 1.39% |

updated 候选中约 **68.06%** 的清洗后正文少于 20,000 字符；同时仍有 25 篇不少于 100,000 字符，后续算法需要保留工作预算，不能只按整篇相似度处理超长文档。

## 复现

```bash
PYTHONPATH=src .venv/bin/python scripts/mdcompare_candidate_stats.py \
  --report tmp/mdcompare-knowledge-research-20261107/filter-exploration-20261108/structural-normalization-v3-uat/full-primary.json \
  --baseline tmp/mdcompare-uat-all-20261107-130000/results/baseline-0.json \
  --incoming tmp/mdcompare-uat-all-20261107-130000/results/incoming-0.json \
  --output tmp/mdcompare-knowledge-research-20261107/filter-exploration-20261108/candidate-stats-v3/stats.json \
  --markdown tmp/mdcompare-knowledge-research-20261107/filter-exploration-20261108/candidate-stats-v3/REPORT.md
```

脚本：[`scripts/mdcompare_candidate_stats.py`](../scripts/mdcompare_candidate_stats.py)。
