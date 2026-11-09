# 05: 完成正式UAT、安装与使用交付

Type: task
Status: claimed
Blocked by: 04

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

对全部规格执行仓库check/build/隔离安装smoke，修复发布遗漏；knowledge源码随wheel安装，dogpdkb discover --snapshot、sync --knowledge和mdcompare knowledge离线真实CLI一致。扩展既有smoke_install即可，不加新测试缝。不发布不部署远端。

使用冻结11月7日/9日私有完整元数据+6篇raw从正式产品命令复跑日常UAT并记录2new、4timestamp、1字节相同、3正文差异；不得写入真实正文到git。从三篇已审计变化/旧限制准备私有固定cases与验收记录，不能拿抽象模型审计当已验证Graphify输出；未授权真实提取不调用模型，采用确定的外部替身验证正反例并明确没有真实重编译。

新增用户指南说明观察/语料/graph基线、初始化、规划、审批绑定、待办、质量gate、失败恢复、计数/费用限制和旧入口兼容；更新原实现盘点和README相关入口。只有check/build/安装/UAT全通过才resolved，不可把失败当文档局限。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。

## Answer

> 当前复核：原门禁在手动安装未声明的 `networkx` 后才通过，干净 dev bootstrap 尚未验收；本票暂回 claimed，修复并复验后方可重新 resolved。以下保留原报告和失败证据。

### 集成与交付合同

- 从 `daily-knowledge-sync` 的 `6ef44b4b7230dcb9661dd14dd3b64913e6065fcf` 出发，以 `--no-ff` 保留上一 coder 的 `4542859c74e51b541bcc9ea2df375ef12d4e4a57`，合并提交为 `5d804594a0f9d86b6b051dc41f99a01e6e13e6ba`。最终门禁前再次合入 `daily-knowledge-sync`，当时无更新；本票结果仍需合回集成分支。
- 保留正式安装 smoke、wheel/sdist 模块 gate、`--graphify-python` 虚拟环境符号链接回归与[用户指南](../../../docs/daily-knowledge-sync.md)。正式契约是 `dogpdkb discover --snapshot` 采集、`sync --knowledge` 默认只规划、显式审批并通过有界质量门禁后才执行完整来源；独立 `mdcompare knowledge` 做离线比较。旧命令默认行为不变。
- 本轮未重做已有实现或私有 UAT，也未新增源码修复；原有解释器修复及回归由 `4542859` 保存，开发期 red 命令未在本轮证据中，不能补写为已观察事实。

### 最终门禁与离线环境

- `UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never make check`：**910 passed**，文档门禁 **63** 篇、Ruff check 通过、**169** 文件格式检查通过。
- `UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never make smoke`：通过，实际构建 `dogpdkb-0.1.6.tar.gz` 和 `dogpdkb-0.1.6-py3-none-any.whl`，在全新临时 venv 从 wheel 安装并运行真实 CLI，覆盖离线 snapshot、plan、审批、质量正反例及 managed 安装/升级/卸载；外部 Graphify 和安装下载运输使用替身。临时 venv 在 smoke 结束后自动清理，不是生产部署。
- 离线环境的首次 `uv sync` 因缓存缺少锁定的 Ruff 轮子失败；使用仓库 `scripts/bootstrap.py --seed` 从本机已安装的锁定版本复用依赖，再离线 `make bootstrap`。首次 `make check` 出现 **7 failed / 903 passed**：这 7 项现有 Graphify 替身测试使用当前虚拟环境，候选阶段实际缺少 `networkx`；对保留的合成测试夹具直接复现 `ModuleNotFoundError`。从已有 UV 缓存离线安装 `networkx==3.6.1` 后，`UV_OFFLINE=1 .venv/bin/python -m pytest -q tests/test_knowledge_execute.py` 为 **13 passed**，随后的完整门禁如上。未下载包或放宽门禁；这不是源码 red/green 修复。默认 dev bootstrap 本身未声明该测试所需的 Graphify 环境依赖，冷环境需另行准备，后续可明确测试依赖。
- 构建包和本次私有验收日志（含失败、诊断、复验、包 SHA-256）保存在 `/tmp/daily-knowledge-sync-ticket05-final.H9DDAjhR/`。离线环境就绪前的 4 文件 LSP probe 为 inconclusive；bootstrap 后重新主动探测，同样 4 文件 **clean=4、findings=0**，不把第一次结果当 clean。

### 已有私有 UAT 的只读核验

- 原输入和产物：`/tmp/daily-knowledge-sync-ticket05-uat-v1/`；本次复核日志：`/tmp/daily-knowledge-sync-ticket05-final.H9DDAjhR/private-uat-verification.log`。未调用其固定旧 cwd 的私有脚本，也未重新准备真实正文。
- 初始化 **7,548** 身份、选中 **0**；当前 **7,550**，**2 new / 4 updated / 6 去重导出 / 0 失败**。历史原始字节审计记录为 **1 same / 3 正文变化**；保留的 `offline-knowledge.json` 独立比较结果是 **1 REUSE / 3 REVIEW（正文候选）**，不是声称其 status 为 `DELTA`。两项 `detail_newer_than_listing` 在正式计划中均为 `HOLD_EXISTING`；计划有 7,550 条，未批准或自动提取。
- 前两篇合格旧文的正式执行报告为 **2 已批准 / 2 图谱成功 / 4 个显式案例通过**。本次只读核验留存候选中 4/4 案例、已发布隔离图与候选字节相同、两篇 captured/frozen/execution 完整 raw 均逐字节相同。负例候选独立验证遗漏 `old-qoder-refresh`，拒绝图与留存备份字节相同且无发布结果，拒绝状态仍保持旧图基线；执行前后 state 字节不变由原 UAT summary 记录，本次没有独立的执行前 state 副本可逐字节复核。
- 两次外部调用及 token 是确定性替身报告，费用 `unknown`；第三篇因版本异常 HOLD 未执行。没有真实模型或真实语义提取，固定案例只证明有界断言，不能证明全部知识保留或生产图正确。无生产同步、远端发布或基线推进。完整上下文见 [UAT 记录](../../../docs/daily-knowledge-sync-uat.md)。
