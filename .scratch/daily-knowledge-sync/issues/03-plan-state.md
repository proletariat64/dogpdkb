# 03: 接通只读知识规划与三类版本状态

Type: task
Status: resolved
Blocked by: 01, 02

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

在dogpdkb sync现有入口提供--knowledge分支（默认规划，不执行旧apply/OCR/Graphify），复用02快照和01比较。新增可安装knowledge_sync/knowledge_plan等深模块，不要求复制现有pipeline。规划支持--snapshot、--state、--staging、可选--baseline（mdcompare清单）；远端采集用02同模块。新增版本化流程报告及快照、语料成功、图谱成功的独立逐文档状态。graph比较相对最后成功图谱来源而不是最新观察；支持从显式raw baseline首次登记graph来源，不猜旧富化为raw；首次无baseline输出未覆盖待核不全库提取。

KEEP保留旧知识且独立元数据任务；HOLD/UNKNOWN不调用外部；候选仅证据不是许可；新ID单列且无图时不可宣称覆盖。pending包括拒绝/失败/未授权，后续远端没再改也保留。报告每身份一个结果、守恒计数、原因、来源位置、非原子时间、分类与正文差异及无费用证据则unknown；私有原子输出，拒绝输出/状态覆盖输入。输入版本绑定用私有完整raw快照和直接字节比较，不新建hash。

为04定义明确的plan/state/approval契约及入口分发，规划源码不接提取。可在knowledge_sync main接--execute/--approvals/--quality-cases解析然后委托04的knowledge_execute模块，但先确保默认无执行，合同写Answer。拥有sync_knowledge_base.py的最小--knowledge分发、新planner/state模块、黑盒tests/test_knowledge_sync.py。不修改02拥有的capture除必要兼容。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。

## Answer

### 公开 CLI 与默认边界

```bash
dogpdkb sync --knowledge --snapshot <knowledge-capture.json> --state <private-state.json> --staging <new-private-plan-dir> [--baseline <raw-mdcompare-manifest.json>]
```

`sync` 在读取普通同步配置、检查 Graphify 解释器或进入 apply/OCR 之前分发 `--knowledge`。默认只生成 `<staging>/plan.json` 并原子更新 `--state`；已有 plan、输入/输出碰撞、缺页或身份不完整会拒绝，完整 listing 下的逐文档异常不会抹掉其他正常结果。plan/state 为 0600，staging 为 0700。未执行网络、OCR、Graphify、发布或基线推进。

显式执行入口已解析 `--execute --approvals <file> --quality-cases <file>`，仅此时延迟导入 `dogpdkb.knowledge_execute.run_execution(args)`；本票未实现执行器，当前安全退出 1 且不写 plan/state。04 可在新 staging 调用 `run_planner(args)`，续跑时读取并校验既有 plan，不能绕过批准直接调用提取。

### `knowledge-plan-1.0`

- 顶层含 `book/observation/snapshot_status/results/baseline_only/counts/execution/approval_contract/cost`。当前完整 listing 的每个文档恰好一个 result；`baseline_only` 只表示当前列表缺席的已登记图来源，不表示删除。
- 每个 result 含 `book_id/document_id/labels/selected/action/comparison/observation/corpus/graph/reason_codes/pending/metadata_tasks/approval/execution`。`comparison` 是 v4 的逐单元来源位置与比较结论，不包含默认正文证据。
- KEEP 只复用知识；renamed/moved 仍生成独立 `metadata_tasks`，未完成维护时不推进图谱版本。HOLD/UNKNOWN 不产生执行材料。BODY_DELTA_CANDIDATE 只产生审批材料，不是许可。
- 候选 `execution` 合同为 `{source_scope: whole_document, source_version, raw, previous_graph_version, review_issues}`。raw 指向 plan 同目录下 `inputs/<book>-<id>.md` 的私有完整冻结正文；revision、old-only、context、presentation 和 unresolved 均进入 `review_issues`。不使用 diff 或统计视图替代完整来源。
- `counts` 分开记录 results、selected、compared、new_documents、pending、actions、approved、executions、external_calls、cache_hits 和 graph_successes。规划阶段后五项均为 0；无费用证据时 `cost.status=unknown`。

### `knowledge-state-1.0`

`documents[<id>]` 独立保存：

- `observation={version,observed_at}`：最近一次完整列表观察，不等于语料或图谱成功。
- `corpus={status,version,raw}`：只有 raw 文件实际存在且无导出/列表详情版本异常时才为 captured；raw 缺失、失败或非原子异常为 pending/not_captured。
- `graph={status,version,raw,metadata,coverage}`：始终是最后成功图谱来源或明确 uncovered。显式 baseline 仅接受 `content_kind=raw`，登记为 `registered_raw_baseline`；不会把历史富化伪称 raw。无 metadata/pending 的 KEEP 可记为 `body_reuse` 并覆盖等价新来源；候选、HOLD、拒绝、失败和未授权均不前移 graph。
- `pending` 为稳定原因码数组。候选和新 ID 在后续远端无变化时仍重新生成审批材料；其他拒绝/失败/复核原因继续 HOLD。graph 比较只使用此处的图谱来源，不使用最新 observation 或 corpus 代替。

state 与 snapshot 的 raw 引用均相对各自 JSON 所在目录解析，绝对路径仅用于跨目录引用。输入版本通过不可变快照、候选冻结副本和直接字节比较绑定，没有新增 hash。

### 04 审批合同

`approval_contract.schema_version=knowledge-approvals-1.0`。审批文件约定：

```json
{
  "schema_version": "knowledge-approvals-1.0",
  "plan": "relative/path/to/plan.json",
  "approvals": [
    {
      "book_id": 123,
      "document_id": 456,
      "source_version": {"field": "content_updated_at", "value": "...", "utc": "..."},
      "raw": {"path": "inputs/123-456.md", "stage": "raw_export"},
      "source_scope": "whole_document",
      "review_dispositions": [{"code": "REVISION_REVIEW_REQUIRED", "decision": "acknowledged"}]
    }
  ]
}
```

04 必须将 book/id、完整 version、raw 和 whole-document 范围与所选 plan 的候选精确匹配；raw 相对 plan 解析。执行前直接比较当前 snapshot raw 与 plan 冻结 raw 字节，变化即失效；`review_dispositions` 必须覆盖该候选全部 `review_issues`，并通过 `--quality-cases` 提供有界新旧事实门禁。候选、KEEP、HOLD 均不隐式生成批准。

### TDD 与验证

- 初始 CLI red：现有 sync 拒绝 `--knowledge`；加入分发和未登记图谱状态后 green。
- raw baseline 复用 red：仍返回 HOLD；接入 v4 图谱来源比较后 KEEP/body_reuse green。
- 候选合同 red：`execution=None`；冻结完整 raw 并加入版本/复核合同后 green。
- pending red：未变化观察把候选降为 HOLD；保留可续审候选及新 ID 原因后 green。
- 新 ID red：缺少独立原因与计数；补充后保持 uncovered green。
- REVIEW red：修订候选 `review_issues=[]`；加入强制修订复核后 green。
- 初次观察 red：较新 observation 被误当旧图已覆盖；无当前 raw 时改为 HOLD/pending green。
- 输出与缺失 raw red：已有 plan 被重写、缺失 raw 被误记 captured；加入拒绝覆盖和逐文档 pending 后 green。
- `snapshot.status=incomplete` 且 listing 完整时，正常文档仍完成 KEEP/比较，只有详情异常文档 HOLD/pending；`listing.complete=false` 则整体拒绝。

最终验证：`test_knowledge_sync.py + test_knowledge_capture.py + test_pipeline.py + test_platform.py + test_mdcompare_knowledge_cli.py + test_mdcompare_knowledge_units.py` 共 **195 passed**；4 个变更 Python 文件 Ruff check、Ruff format --check 和 LSP error 检查通过。显式 `--execute` 当前退出 1，state/plan 均不存在。未做 05 的安装或真实 UAT。
