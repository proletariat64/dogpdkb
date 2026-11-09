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

- 01–03 集成后完整 `make check`：896 passed，Ruff检查及165文件格式检查通过，61篇文档门禁通过。这是 04 合入前的结果，不是整份规格最终验收。
- 04 已合入，合同与执行边界见 [04 Answer](issues/04-approved-execution.md#answer)；实现提交 `6a6b158`，分支指针 `2a8dba8c1d3d488421dad922b84b042666698b46`，合并提交 `d3101be5fea3963ecceb6d30238cb0f100cad82b`。集成验证：`test_knowledge_execute.py`、`test_knowledge_sync.py`、`test_knowledge_capture.py`、`test_update_graph.py`、`test_pipeline.py` 共 **69 passed**；4 个变更 Python 文件 Ruff check 和 format --check 通过。质量门禁仅覆盖显式绑定的有界案例，不证明全部知识无损；元数据改名/搬动无法安全维护时 HOLD，不以重提取规避。当时并发与输入保护缺陷阻塞 05；后续修复及验证见下。
- [04 resolved](issues/04-approved-execution.md#安全修复与集成复验)：实际修复及回归提交 `9517bc75dd32399e9ca695edc875ee89f99ebcdb`，合并及测试提交 `aa1ff1242ebfd16142db190c9d75322b293f90d4`。执行、所有 sync、capture、update_graph、pipeline 和 mdcompare（含 strict CLI）聚焦 **456 passed**；指定别名、发布前 state 漂移、发布后 state 失败恢复及 strict CLI 回归 **8 passed**；仓库 Ruff check 通过。该轮 4 文件 LSP probe 为 inconclusive，不作为 clean 证据。当时 05 验收尚未运行，后续结果见下。
- [05 resolved](issues/05-acceptance-release.md#answer)：验收收尾分支保留 `4542859c74e51b541bcc9ea2df375ef12d4e4a57` 和冷环境替身修复 `29f6bb6da22287aae6ecb87dda37f3e15decec16`，以 `--no-ff` 合入集成分支，合并提交 `c07d410ca13a21b6814a2cd541cf64a2762d217b`。最终 Standards/Spec code-review **待执行**，本次只合并和核验。
- 05 冷环境历史证据：原 check 手工安装未声明的 `networkx`；测试 Graphify 替身现自带最小 shim，不手装 NetworkX，red **7 failed / 6 passed**、修复后执行测试 **13 passed**。干净 dev 环境完整 check **910 passed**、文档门禁 **63** 篇、Ruff 和 **169** 文件格式检查通过；离线 build/隔离安装 smoke 通过。共享冷日志：`/tmp/daily-knowledge-sync-ticket05-fixture.VNKBoo2Z/make-check.log`、`/tmp/daily-knowledge-sync-ticket05-fixture.VNKBoo2Z/make-smoke.log`（同目录保留 bootstrap、red/green、环境及包哈希）。本次未重建或重跑 smoke；历史 LSP 在环境就绪后复探 4 clean。
- 集成分支合并后 `PYTHONNOUSERSITE=1 UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never PIP_NO_INDEX=1 make check` 实测 **910 passed**、文档门禁 **63** 篇、Ruff 和 **169** 文件格式检查通过，与冷环境报告一致；日志 `/tmp/daily-knowledge-sync-integration.l5YhvWpj/make-check.log`。
- 私有 UAT summary 与保留实物只读比较：**7,548 → 7,550**、初次选中 0、**2 new / 4 updated / 6 捕获 / 0 失败**；4 篇旧 raw **1 字节相同 / 3 字节不同**，独立比较结果 **1 REUSE / 3 REVIEW**，2 个详情异常 HOLD。执行报告与 summary 均为 **2 获批 / 2 图谱成功 / 4 案例通过**，发布图等于候选、拒绝图等于备份；拒绝时 state 字节不变仅由原 summary 记录，缺执行前副本，不能独立复核。外部 Graphify 为确定性替身，固定案例有界，无真实模型或生产操作；本次比较日志 `/tmp/daily-knowledge-sync-integration.l5YhvWpj/uat-comparison.log`，历史日志 `/tmp/daily-knowledge-sync-ticket05-final.H9DDAjhR/`，原私有产物 `/tmp/daily-knowledge-sync-ticket05-uat-v1/`。

## Fog

- 保留旧compare契约；没有明确生成来源时保护字面代码，不用OCR内容推断来源。
- 实际真实Graphify编译需另行授权；替身测试和历史十篇实验不得冒称新流程已通过真实生产验收。
- **已关闭（历史复现）**：04 合并检查使用隔离临时目录和 Graphify 替身复现最终审批核验后、发布前 state 变化导致先发布后发现漂移，续跑因计划版本冲突被拒；现有协作锁及发布前检查，候选构建期间漂移回归已通过。非协作写入仍不受锁保证。
- **已关闭（历史复现）**：将审批输入放在 `<staging>/execution-manifest.json` 曾导致执行覆盖审批文件后仍报告成功，重跑失败；现已增加输入/输出碰撞检查，并通过直接碰撞和别名回归。
