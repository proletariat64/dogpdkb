# 日常知识增量同步指南

设计更新：现有知识库初始化、单实例约束、逐步骤写入 `.sync-state.json` 和精确时间比较见 [sync 状态设计](sync-state-design.md)。该设计尚未实现；本文下述 raw 登记和独立 state 文件要求描述现有实现，实施新设计时需要同步迁移。

本文说明 `dogpdkb discover --snapshot` 与 `dogpdkb sync --knowledge` 的显式正文更新流程。该流程默认只采集和规划；候选文档不会自动调用 Graphify。不带 `--knowledge` 的既有同步、OCR 和发布入口保持原行为。

## 1. 三种版本不要混用

每篇文档分别记录三类状态：

- **观察版本**：最近一次完整远端列表看到的版本，只证明“看过”。
- **语料版本**：已成功保存的 raw 导出，只证明“已取得正文”。
- **图谱来源版本**：最后成功复用或整来源发布的版本，才表示现有图覆盖该正文。

列表时间变新不等于正文变化，raw 下载成功不等于图谱更新。候选、拒绝、异常和失败都会留下 `pending`；下次远端时间没有继续变化时，待办仍保留。

## 2. 初始化完整观察基线

```bash
dogpdkb discover -s <book-url> \
  --snapshot <private>/baseline.json --staging <private>/baseline-capture
```

初次没有 `--baseline-snapshot` 时，命令只保存完整列表，状态为 `initialized`。它不会把全库文档算作一天新增，也不会自动导出或提取全库。

输出包含知识库和文档 ID、UTC 观察起止时间、分页覆盖、实际使用的时间字段及完整性。缺失时间、时间倒退、分页缺口或导出失败不会形成可信的“零变化”结论。列表中缺席只记为 `absent`，不据此确认删除。

## 3. 采集日常增量

```bash
dogpdkb discover -s <book-url> \
  --baseline-snapshot <private>/baseline.json \
  --snapshot <private>/current.json --staging <private>/current-capture \
  [--pending-state <private>/previous.json]
```

`new`、`updated`、`renamed`、`moved` 分开报告；同一 ID 的重叠标签只导出一次。选中正文保存为私有 raw，后续审批绑定这个实际版本。列表和详情时间分别保留；两者不一致时该文档进入异常和待办，不伪造原子快照。

离线重放使用同一正式入口，并提供完整本地适配器：

```bash
dogpdkb discover -s <book-url> --config <config.toml> \
  --snapshot <private>/current.json --baseline-snapshot <private>/baseline.json \
  --staging <private>/current-capture \
  --listing <complete-listing.json> --details-dir <private-details>
```

本地列表被选中但详情缺失时直接失败，不会静默联网补缺。

## 4. 生成只读正文计划

```bash
dogpdkb sync --knowledge \
  --snapshot <private>/current.json --state <private>/state.json \
  --staging <private>/planning [--baseline <raw-mdcompare-manifest.json>]
```

可选 `--baseline` 是显式的 raw 图谱来源登记清单，每项必须有 `content_kind=raw` 和对应知识库/文档 ID。不要把历史富化文件当成 raw。首次没有图谱来源登记时，文档保持未覆盖和待核，不进行全库提取。

主要路由：

- `KEEP_EXISTING`：正文按当前规则复用；改名或搬动仍有独立元数据任务。
- `BODY_DELTA_CANDIDATE`：保存完整冻结 raw、变化单元和旧侧证据，等待审批；不是提取许可。
- `HOLD_EXISTING`：保留旧图和待办，不自动提取。旧侧缺失、上下文不确定、边界异常及版本异常均可能进入此路由。

计划固定 `plan_only=true`、`automatic_extraction_allowed=false`、`full_document_fallback=false`。规划器先冻结新侧及已登记的旧侧 raw，再比较这两个冻结视图；候选执行同时绑定新旧字节，任一侧后续漂移都会使许可失效。独立离线 `mdcompare knowledge` 仍可比较冻结 raw，但不修改 dogpdkb 状态或图谱。

## 5. 审批和质量门禁

审批使用 `knowledge-approvals-2.0`，绑定当前 `knowledge-plan-2.0` 的 `plan.json`。每项必须精确匹配知识库、文档 ID、完整 `source_version`、冻结 raw 和 `source_scope=whole_document`。`review_issues` 按文档或单元索引、来源行生成稳定 `issue_id`；审批必须用相同 ID 逐项处置，不能以原因码通用确认代替。修订使用 `accept_revision`，上下文问题使用 `acknowledged`；旧侧未再观察到的规则必须逐项选择 `approve_removal` 或 `preserve`。`preserve` 还须列出同一文档、同一来源版本的 `quality_case_ids`。旧版通用 ack 不自动升级，会被拒绝。

质量文件使用 `knowledge-quality-cases-2.0`，同样绑定计划。每个获准文档至少一个案例；案例绑定相同来源版本，可对 `node` 或 `edge` 做同记录精确匹配及必要字段片段断言。应同时覆盖本次确认的新事实和必须保留的旧限制。外部文件中的 `pass` 字段没有授权作用。

门禁只证明显式案例通过，**不证明全部知识无损**，也不能用节点数增加代替。

## 6. 显式执行与恢复

```bash
dogpdkb sync --knowledge --execute \
  --snapshot <private>/current.json --state <private>/state.json \
  --staging <private>/planning --approvals <private>/approvals.json \
  --quality-cases <private>/quality.json --graph-dir <existing-graphify-out> \
  [--graphify-python <graph-venv/bin/python>]
```

只执行已批准的候选，并把完整冻结 raw 交给既有整来源替换事务；diff、候选块和统计视图不能替代完整来源。新 ID 单独审核，不宣称全库知识新增。已有图基线必须存在；元数据任务无法安全维护时保持 HOLD，不靠重编译规避。

候选图先写隔离目录，在有损合并前检查提取及缓存结果的节点身份、边/超边引用，再检查完整来源覆盖、旧来源归属、无关来源记录和固定质量案例；冲突无法安全保留时拒绝候选。失败不改旧图或图谱成功版本。续跑复用已有缓存和候选；发布前 journal 保存原/目标状态、候选及审批绑定。图或 state 已提交而报告因硬退出缺失时，直接核验 journal、候选、图谱和 state 字节后重建报告，不重复提取。协作锁只协调遵守该锁的写入者，不是任意外部写入的全局事务。

`--graphify-python` 可以是虚拟环境的 `bin/python` 符号链接；执行器保留该路径以加载对应环境。

## 7. 报告、成本和交付边界

`knowledge-plan-2.0`、`knowledge-state-1.0`、`knowledge-execution-1.0` 分开报告观察、语料、候选、批准、执行、缓存命中、外部调用和图谱成功数。当前观察、语料或 pending 已更新的 state 会使旧 plan 失效；已恢复的采集/比较瞬时错误会在成功重试时重新计算，未知或人工未处理问题仍保留。token 和调用数只采用适配器实际返回的非负整数证据；缺失或非法值为 `null`/`unknown`，只有实际返回或无提取时才能记录 0。没有账单时费用为 `unknown`，字符量或节点数不能换算成真实节费。

私有快照、raw、状态和报告使用受限权限，默认日志不包含正文证据。不得覆盖输入或用输出别名绕过保护。

开发验收使用 Python 3.12 和 uv，先运行 `make bootstrap` 创建隔离 dev 环境。交付门禁：

```bash
make bootstrap
make check
make smoke
```

执行测试的确定性外部 Graphify 替身自带最小 NetworkX shim，无需在 dev venv 或系统环境中手工安装 NetworkX；该 shim 不随产品安装，也不是真实 Graphify。真实执行仍须在所配置的 Graphify 解释器中安装其依赖，包括 NetworkX。

`make smoke` 从 wheel 在临时环境执行真实安装后的 CLI，覆盖快照、规划、审批、质量失败拒绝和成功执行，并使用确定的外部 Graphify 替身。它不运行真实模型、不访问生产图谱。实际部署、生产同步、付费提取及基线推进仍需单独授权。
