# 日常知识增量实现地图

## Notes

- 集成分支：`daily-knowledge-sync`；规格：[spec](spec.md)。
- 初始未提交工作已逐文件备份到仓库外；全部现有源码、测试、报告纳入保留基线，不删除或覆盖私有实验。
- 测试缝：既有mdcompare、dogpdkb discover和sync CLI；不用真实语雀/Graphify，不部署生产。
- 实现票各自worktree，合并由merger执行；完成后进行Standards/Spec双轴审查。

## Tickets

- [01 正式正文范围](issues/01-body-view.md) — none
- [02 完整观察与导出](issues/02-observation-capture.md) — none
- [03 规划与三类状态](issues/03-plan-state.md) — 01, 02
- [04 审核执行与发布](issues/04-approved-execution.md) — 03
- [05 UAT、安装与文档](issues/05-acceptance-release.md) — 04

## Decisions-so-far

- 五票覆盖已批准规格；审批执行开发被本次implement-spec授权，但真实生产/付费动作仍未授权。
- 01 已合入：正文使用 `body-units-v4`；代码内生成区须有 `generated-regions-v1` 声明并验证位置，未声明的代码标记保留为字面内容；无效声明或无法配对的边界返回 UNKNOWN/HOLD。保留 `knowledge-1.0` 既有字段/退出码及旧 compare 行为。详情与上下文见 [01 Answer](issues/01-body-view.md#answer)；实现提交 `0e94797d796d007e5cde56a5d00fa27ebd63b4d9`，合并提交 `7c6f33b1649648e74c995cfbb4cb4cfb3aa137ee`。集成验证：指定知识比较测试 146 passed，8 个变更 Python 文件 Ruff check 通过。
- 02 已在独立分支实现并复验：`dogpdkb discover --snapshot` 输出 `knowledge-capture-1.0`，支持完整观察、增量分类、选中 raw、pending 与私有续跑；初次观察不全库导出，完整基线不可覆盖。03 直接按 [02 Answer](issues/02-observation-capture.md#answer) 消费 documents/changes/exports/pending 和相对 raw 引用。合入 01 后聚焦验证 195 passed。合并提交 `656ee3384096068374f3336cfcd64abaec37315c`；集成验证：`test_knowledge_capture.py`、`test_sync.py`、`test_mcp_recovery.py`、`test_mdcompare_knowledge_cli.py` 共 110 passed；3 个变更 Python 文件 Ruff check 通过。
- 03 已合入：`sync --knowledge` 默认只规划，独立记录观察、语料和图谱状态，候选需显式批准；04 执行及审批合同、状态与恢复细节见 [03 Answer](issues/03-plan-state.md#answer)。实现提交 `590781c6e5615091bd75ea2a3389839890ea083e`，合并提交 `9c1ea491dae1f982a2afafd6e9b2da68282c9882`。集成验证：`test_knowledge_sync.py`、`test_knowledge_capture.py`、`test_pipeline.py`、`test_platform.py`、`test_mdcompare_knowledge_cli.py`、`test_mdcompare_knowledge_units.py` 共 195 passed；4 个变更 Python 文件 Ruff check 和 format --check 通过。04 的 03 集成门禁已通过；未执行生产操作。

## Fog

- 保留旧compare契约；没有明确生成来源时保护字面代码，不用OCR内容推断来源。
- 实际真实Graphify编译需另行授权；替身测试和历史十篇实验不得冒称新流程已通过真实生产验收。
