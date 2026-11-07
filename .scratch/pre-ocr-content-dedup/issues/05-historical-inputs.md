# 05: 历史富化输入的保守比较

Type: task
Blocked by: 02
Status: resolved

**What to build:** 调用者明确声明历史 OCR 富化旧输入后，可以看到差异及来源限制；缺失旧 raw 或无法区分派生转写时得到 UNKNOWN，避免把真实删除解释成无变化。

- [x] 历史富化内容绝不隐式当成 raw；缺旧 raw、未标记转写、OCR block 来源不完整分别有明确原因码。
- [x] 只根据可验证标记及完整来源映射识别派生 OCR 内容；不靠任意正则、相似度、包含关系或人工业务分类恢复所谓旧 raw。
- [x] 未能建立可验证原始视图时返回 UNKNOWN；可展示原始文本差异，但明确其为历史富化与 raw 的不对称比较，不把消失文字强判为源文删除或确定未变。
- [x] 保留可以独立确认的局部变化与来源证据；历史处理本身不得绕过资源和 OCR 复用验证。
- [x] CLI 黑盒覆盖无 raw、仅未标记 OCR 转写、明确 block 但来源不完整、可验证来源映射，以及新文包含于旧文但规则真实删除的反例。
- [x] 本票无需等待完整 OCR 复用动作实现；来源识别与保守 UNKNOWN 路径可独立验收。

## Answer

Implemented explicit historical input handling with bounded stage-to-stage Myers
explanations and separate provenance verification. Unknown history retains its
observed differences and distinct missing-raw, unmarked-content, incomplete-map,
read-failure or reconstruction reasons. A retained raw snapshot plus complete
`mdocr-raw-snapshot-v1` image/block mapping is verified by exact reconstruction;
only then do text and resource discovery receive the raw source views. No
similarity, containment, arbitrary cleaning or production mutation is used.

TDD evidence: unmarked-history CLI test failed first on missing comparison scope,
then passed with asymmetric explanation. Marked-without-map test failed on its
missing provenance reason, then passed. Positive source-map rule-deletion test
failed while history remained UNKNOWN, then passed after exact reconstruction.
Missing raw evidence first lost the observed diff, then passed after retaining
stage evidence. Malformed block reference is handled without traceback. The
parallel input-safety contract rejects illegal file paths globally; this ticket
retains the per-item UNKNOWN behavior for valid paths whose evidence is absent.

Validation after merging integration resource and input-safety changes: 111
focused CLI tests passed, including 14 historical cases; Pyright reported zero
errors/warnings for mdcompare source and new tests; Ruff and doc-guard passed.
The initial real-file development run used all 98 fixed historical/raw pairs,
returned exit 2 with 98 UNKNOWN and explicit asymmetric scope, produced bounded
evidence for 87 and correctly recorded diff-budget exhaustion for 11 at the
default work budget. Its report is private and remains outside Git; final UAT is
owned by ticket 07 and must not interpret manual classifications as action gold.

Contract: [historical input documentation](../../../docs/mdcompare-history.md).
