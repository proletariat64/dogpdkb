# 04: 实现审核后完整来源提取与安全发布

Type: task
Status: resolved
Blocked by: 03

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

根据03已落地的plan/state/approval入口，实现knowledge_execute可安装模块与必要的现有update_graph钩子；从dogpdkb sync --knowledge验收。执行必须显式选择批准的book/id、完整raw快照、当前版本、旧侧删除/上下文问题处理及完整来源替换范围；仅审批候选不得隐式忽略REVIEW。批准以后raw变化使许可失效，默认没批准0提取。无图的新文不能KEEP。元数据更新维护稳定doc-id到source映射；做不到安全更新就HOLD，不重编译。

调用现有Graphify整文提取/缓存/整来源事务，输入完整冻结raw而不是diff或统计视图；隔离候选先验证完整来源覆盖、重复ID/悬空边、版本和私有固定新事实/旧限制cases，再发布。支持全新文档与已有图谱但不创建/重建整个生产图。验收案例非仅检查节点数；固定cases为明确有界规则/事实断言，不能让外部任意未绑定的pass文件绕过gate，报告其有界性。缓存/模型调用/token/费用分开，未知不伪造，优先复用已存在提取使用量证据。

逐文档失败保旧图和graph baseline。支持checkpoint/resume，尤其图谱写成功而状态失败能通过直接比较候选/图谱+事务记录恢复不重复计费；并发基线漂移拒绝发布。测试替身只在外部语雀/Graphify边界，拒绝任意命令执行/不需真实付费。主要拥有knowledge_execute模块、tests/test_knowledge_execute.py、update_graph可选参数/入口兼容改动，必要接03入口。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。

## Answer

### 公开执行合同

```bash
dogpdkb sync --knowledge --execute \
  --snapshot <knowledge-capture.json> --state <knowledge-state.json> \
  --staging <existing-plan-dir> --approvals <knowledge-approvals.json> \
  --quality-cases <knowledge-quality-cases.json> --graph-dir <graphify-out> \
  [--graphify-python <python>]
```

执行读取既有 `plan.json`，不重建计划。审批和质量文件均以其所在目录解析 `plan` 引用，必须指向当前 plan。每项审批只允许当前 `BODY_DELTA_CANDIDATE`，精确绑定 book/id、完整 `source_version`、冻结 raw 引用和 `whole_document`；所有 `review_issues` 必须由唯一且明确的 `acknowledged` disposition 覆盖。snapshot 当前 raw 与冻结 raw 直接逐字节比较，state 的图谱来源版本必须与 plan 一致；变化、重复批准、KEEP/HOLD 或带 metadata task 的候选均拒绝，metadata 不通过重提取规避。

质量文件使用 `knowledge-quality-cases-1.0`。每个 case 绑定 book/id/source_version，`record=node|edge`，以 `where` 做同记录精确匹配、以 `required` 的字段字符串列表做同记录包含断言。edge 可用 `source_label`、`target_label`、`relation` 同时绑定主体、客体和关系。每个获准文档至少一个 case；外部 `pass` 字段没有授权作用，结构/绑定错误在提取前拒绝。候选图实际记录必须通过全部案例；门禁仅覆盖显式案例，不证明全部知识无损。

获准来源复制为执行语料中的稳定 `source-[ID].md`，内容是完整冻结 raw，不使用 diff。已有图中同 ID 的旧文件名由现有整来源合并事务清理，已有 `source_url` 按 ID 继承；无关来源保留。全新 ID 可加入既有图，但必须已有图基线，不创建或重建整库。

### 事务、恢复与报告

`update_graph` 新增互斥的 `--candidate-only` 与 `--publish-validated`，旧默认流程不变。候选阶段仍调用现有 `prepare_base/build_candidate`，但不发布；执行器验证完整来源覆盖、重复节点 ID、悬空边/超边、旧来源 ownership 和固定质量案例后，才调用现有 `publish_candidate` 事务。并发图基线变化拒绝发布。

候选、图备份、事务记录及使用证据全部保留。部分提取失败不修改图或 graph state；续跑利用 Graphify 文件缓存。图已发布而 state 写入失败时，续跑复用候选并直接字节核对已发布图，不重新提取。成功后的重复执行只有在审批、raw、质量案例、state、候选和已发布图仍一致时才返回原报告。

`knowledge-execution-1.0` 分开记录 approved、executions、cache_hits、external_calls、graph_successes、tokens、usage 和 cost。缓存根修正为 Graphify API 要求的 `graph_dir.parent`，子进程以 `GRAPHIFY_OUT=<graph-dir名称>` 保持输出目录一致。Graphify 未提供实际调用数时 `external_calls=null` 且 evidence 为 unknown；费用没有账单证据时保持 unknown。

### TDD 与验证证据

- 空批准：初始 CLI 拒绝新参数为 red；实现后零 Graphify、零状态推进为 green。
- raw 变更：初始只报未实现为 red；加入精确审批绑定后在提取前报告 raw 变化为 green。
- 完整来源执行：初始未实现为 red；候选验证、稳定 ID 替换、质量节点/关系断言和状态推进为 green。
- 外部 pass：初始提取后才拒绝为 red；质量定义前置后无 graph-update 为 green。
- 成功后重复执行：初始 state 版本冲突为 red；完整留存证据一致时幂等返回为 green。
- URL 保留：新增断言初始为 None 是 red；按旧图同 ID 继承后为 green。

黑盒替身覆盖旧限制缺失不发布、部分失败缓存续跑、并发基线漂移、图发布后状态故障恢复、新文档加入既有图。最终全量 **905 passed**；执行/规划/采集/管线/图事务聚焦 **69 passed**；仓库 Ruff check、Ruff format --check 和变更文件 LSP error 检查通过。未运行真实网络、模型、OCR、生产发布或 05 安装/UAT。

### 安全修复与集成复验

- 实际修复及新增回归提交：`9517bc75dd32399e9ca695edc875ee89f99ebcdb`；集成测试代码所在的 `--no-ff` 合并提交：`aa1ff1242ebfd16142db190c9d75322b293f90d4`。该修复提交同时包含源码和测试；不使用不存在的 `dc612dd` 作为证据。
- 规划和执行共享 state 协作锁，候选完成后、发布前再次检查 state 字节，发布后检查与恢复仍保留；锁仅协调遵守该锁的写入者，并非针对任意非协作写入的原子事务。
- 执行输出先验证不能覆盖审批、质量、snapshot、plan、state 和关联 raw 等输入；回归覆盖审批输出碰撞、硬链接、符号链接及输出目录祖先碰撞，检查受保护输入不变。
- 在合并提交 `aa1ff1242ebfd16142db190c9d75322b293f90d4` 上，`.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_knowledge_execute.py tests/test_*sync*.py tests/test_knowledge_capture.py tests/test_update_graph.py tests/test_pipeline.py tests/test_mdcompare_*.py`：**456 passed**。其中包含 strict `mdcompare compare` CLI 测试与输入别名回归。
- 单独复验 **8 passed**：`test_knowledge_execute.py` 的 `test_execution_rejects_input_aliases_and_output_ancestors`、`test_execution_outputs_cannot_overwrite_approval_input`、`test_state_change_during_candidate_build_blocks_publication`、`test_graph_publish_then_state_failure_recovers_without_rebilling`；strict CLI 的 `test_mdcompare_cli.py::test_report_cannot_overwrite_document_or_manifest` 与 `test_mdcompare_stability.py::test_reports_cannot_overwrite_referenced_input_aliases`（3 参数）。
- `.venv/bin/ruff check .` 通过；4 个变更 Python 文件的 `.venv/bin/ruff format --check` 通过。
- 本轮对 4 个变更 Python 文件的 LSP probe 均为 inconclusive，不能据此声称 LSP clean。使用隔离临时目录及替身；未运行真实网络、模型、OCR、生产操作或 05 安装/UAT。04 已满足本次集成门禁，05 的其余验收仍须独立执行。
