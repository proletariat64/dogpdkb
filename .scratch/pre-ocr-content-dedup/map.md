# mdcompare implementation map

## Notes

- Scope: approved pre-OCR content comparison spec in this feature directory; CLI name is mdcompare.
- Seven vertical slices were approved by the user. Each ticket owns its CLI acceptance tests.
- Work the frontier: a ready-for-agent ticket may start when all its blockers are resolved. Claim with Status: claimed; finish with Status: resolved plus an Answer containing verification evidence; append its context pointer to Decisions-so-far here.
- Implementation is active on integration branch pre-ocr-content-dedup. No production integration, OCR execution, baseline advancement or Graphify work is authorized by these tickets.

## Tickets

- [01: 独立 CLI 与无资源文档比较](issues/01-cli-raw-comparison.md) — blocked by: none
- [02: 文本变化解释与有限规范化](issues/02-text-diff-normalization.md) — blocked by: 01
- [03: 资源独立比较与变化资源清单](issues/03-resource-comparison.md) — blocked by: 01
- [04: 验证 OCR 来源并给出复用动作](issues/04-ocr-reuse-decisions.md) — blocked by: 03
- [05: 历史富化输入的保守比较](issues/05-historical-inputs.md) — blocked by: 02
- [06: 固定输入视图与故障报告](issues/06-input-stability-failures.md) — blocked by: 01
- [07: 真实文件 UAT 与最终交付验收](issues/07-real-uat-delivery.md) — blocked by: 02, 04, 05, 06

## Decisions-so-far

- [01 resolved](issues/01-cli-raw-comparison.md): merged as adf9e30; standalone CLI foundation, schema 1.0, private reports and 16 black-box tests. Parallel frontier 02, 03, 06 assigned independent worktrees.

- CLI name: mdcompare. Input comparison is offline and read-only; evidence gaps stay UNKNOWN.
- Real UAT uses the co.bot package at `/home/admin/workspace/dogpdkb/tmp/mdcompare-uat-20261107/`; start by reading its README. Access, 98 old/new/diff files each, and 4,757 index entries were verified during preparation. Recheck availability at execution time.
- Fixed 98 pairs are historical enriched text versus incoming raw, with no complete old/new image-byte evidence. Their manual classifications are not CLI action gold labels.
- Dependencies: 01 unlocks 02, 03 and 06; 02 unlocks 05; 03 unlocks 04; 02, 04, 05 and 06 unlock 07.

## Fog

- The existing real UAT package cannot establish a successful old raw baseline or full image/OCR reuse proof. Record this gap at acceptance; do not fabricate evidence or relax the spec.
