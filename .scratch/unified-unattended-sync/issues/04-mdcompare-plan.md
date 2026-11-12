# 04: mdcompare 整批阶段与自动 plan

Type: task
Status: resolved
Blocked by: 02, 03

## Context

- [规格](../spec.md) mdcompare 与 plan 节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 `02-knowledge-modules.md`（plan 2.0 合同、审批/质量门禁精确位置、必须保留的输入/版本校验、比较器正文/OCR 范围）。工作树代码为准。
- 01 Answer：run 目录/阶段 I/O 信封/handler 接口。02 Answer：capture 复用证据。03 Answer：异常归档 API 与重试抑制合同。
- 现有比较器（mdcompare.knowledge，knowledge-1.0 报告，REUSE/REVIEW/UNKNOWN/HOLD，正文/OCR 范围区分）沿用，不重写算法。旧 plan 在 run_planner 内联比较；新规格要求整批 mdcompare 阶段先完成、plan 只消费。
- 关键现状（见 00-base-context / 笔记 03）：比较器实际 STATUSES=(REUSE,DELTA,REVIEW,NO_BODY,UNKNOWN)，GRAPHIFY_ACTIONS=(KEEP_EXISTING,HOLD_EXISTING,BODY_DELTA_CANDIDATE)。**knowledge planner 目前传 resources=[]，OCR 维度从未被行使**——本票必须把 resources 接入 plan，产出 OCR 工作名单（基于 mdcompare `prepared-jpeg-ocr-v1` 的 reusable/processing_required），并保证正文复用与 OCR 独立。图片变化由 resources.py 字节相等/s3-version-id-v1 + decisions.py choose_action 独立判定。

## Acceptance and ownership

1. **mdcompare 阶段**：整批离线比较，输出每篇结果（沿用比较器及其正文/OCR 范围）；注册为 runner handler。plan 消费这些结果，不重复比较，不调用 OCR/Graphify。
2. **自动 plan（取代审批合同）**：明确正文复用来源保留已有正文知识；新文档、正文增量、缺少旧输入或比较不确定（REVIEW/UNKNOWN/HOLD）的来源，在当前完整 raw 可用时自动进入完整文档编译名单。区分"比较不确定"（可完整重编译）与"当前输入不可用"（归档异常，不编造输入）。不建独立审批阶段/复杂自动审批条件/逐项人工处置/语义质量门禁；保留现有底层输入/版本校验。
3. **plan 输出**：正文复用、OCR、编译名单、原因、输入引用、先后依赖。元数据维护（改名/搬动/来源引用）纳入同一计划，不因正文复用遗漏（现有仅 HOLD，无执行器——本票补齐计划侧，执行侧 metadata 维护与 06 协调）。正文复用与 OCR 独立：正文相同但图片需处理仍进 OCR 名单；成功 OCR 改变编译输入时允许该来源进 compile。允许 OCR 不完整时用当前可用内容继续编译，不在 OCR 后生成第二套审批计划。
4. **重试抑制**：消费 03 合同——同版本已归档失败不每日重试；新版本正常处理。
5. **阶段接线**：注册 mdcompare + plan handler；plan 阶段产物合同（含 OCR 结果引用 schema，基于现有 mdocr 结果格式）在 Answer 公布，供 05/06 消费。执行阶段记录实际最终输入与缺失项的字段在此定义。
6. **测试改写边界**：只改写被规格取代的人工审批/语义质量阻断断言；其余输入完整性/故障恢复回归保留。

主要拥有：knowledge_plan.py、mdcompare 阶段 handler、knowledge_sync.py 计划路径、相关测试改写。避免改比较器算法（mdcompare.knowledge 核心）、knowledge_execute（06）、OCR（05）。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/04-mdcompare-plan`）；开工前确认基于集成 tip（含 02/03），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 替身；无真实服务/OCR/Graphify/付费。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Answer

### 实现

新增两个 runner handler，沿用比较器算法与旧 plan 的输入完整性 helper，未改比较器核心、`knowledge_execute`（06）、OCR（05）：

- `src/dogpdkb/stages/mdcompare.py`（`HANDLER`）：整批离线比较。冻结 raw（`_freeze_raw` + `validate_outputs` + `state_lock`），从 raw markdown 用比较器自身的 `mdcompare.resources._uses` 派生 resource 声明（resource_id 即引用 URL，不新增指纹），对每篇同时跑正文比较 `compare_knowledge_documents`（knowledge-1.0）与全维度比较 `compare_documents`（text/resources/metadata/ocr）。**resources 已接入**，OCR 维度被行使。产物 `mdcompare/report.json`。
- `src/dogpdkb/stages/plan.py`（`HANDLER`）：只消费 mdcompare 报告，`external_calls=0`，不重复比较、不调 OCR/Graphify。自动产出 body_reuse/compile/ocr/metadata 名单，无审批阶段/质量门禁/逐项人工处置。当前输入不可用→经 03 `archive_anomalies` 归档异常（不编造输入）；同版本已归档失败经 `suppressing_anomalies` 抑制。产物 `plan/plan.json`，并在 `state_lock` 下更新 canonical state（body_reuse 推进 graph coverage）。

旧 `knowledge_plan.run_planner` 审批路径保持不动（01 已弃用转发，08 收敛）；统一 runner 的 plan 阶段即取代审批合同的自动 plan。基线 1094 未破坏。

提交：`56f92c5`(red) → `30f859b`(mdcompare+plan green) → `46434cd`(OCR 名单+验收测试) → `b31dbdb`(先后依赖) → `66b03ad`(metadata/state)。

### 阶段产物合同

信封沿用 01 `unified-stage-1.0`。mdcompare 信封 `artifacts={"report":"mdcompare/report.json"}`；plan 信封 `artifacts={"plan":"plan/plan.json"}`，`inputs={"report":...}`。

**mdcompare 报告 `unified-mdcompare-1.0`**（`mdcompare/report.json`）：
```
{ schema_version, book, observation, snapshot_status,
  documents: [ {
    book_id, document_id, labels[new/updated/renamed/moved], selected,
    source_version, graph_status, previous_graph_version,
    input_available: bool,
    raw: {path, stage:"raw_export"} | null,        # 冻结后的当前输入引用
    previous_raw: {path, stage} | null, previous_kind: "raw"|"historical_enriched"|null,
    metadata: {name, logical_path, source_ref} | null,   # 当前 incoming 元数据
    body: <knowledge-1.0 result row> | null,       # status/graphify_plan.action/units/reason_codes
    comparison: <compare_documents row> | null,    # text/resources/metadata/ocr/action
    resources: [ {resource_id, url, kind:"image"|"attachment"|"media", path?} ],  # incoming 目录
    reason_codes: [...], anomaly: {code, reason} | null
  } ],
  counts: {documents, compared, input_unavailable, external_calls:0} }
```
`input_available=false` 且 `anomaly!=null` 表示当前完整 raw 不可用（`CAPTURE_RAW_UNAVAILABLE`/`CAPTURE_EXPORT_FAILED`）；旧输入缺失记 `PREVIOUS_INPUT_MISSING` 但 `input_available=true`（可完整重编译）。

**plan 产物 `unified-plan-1.0`**（`plan/plan.json`）：
```
{ schema_version, status:"planned", book, observation, mdcompare:<报告相对引用>,
  body_reuse: [ {book_id, document_id, source_version, raw, previous_raw,
                 previous_graph_version, reason:"BODY_REUSE_VERIFIED"} ],
  compile:    [ {book_id, document_id, source_version, raw, previous_raw, previous_kind,
                 scope:"whole_document", reason_codes:[...],
                 ocr_dependencies:[resource_id...], ocr_blocking:false} ],
  ocr:        [ {book_id, document_id, source_version, raw,
                 resources:[{resource_id, url, kind, path?}],
                 reusable:[resource_id...], processing_required:[resource_id...], reason_codes:[...]} ],
  metadata:   [ {book_id, document_id, source_version, raw, tasks:[{field, reason}]} ],
  suppressed: [ {book_id, document_id, source_version, raw, anomaly_id, code, stage, disposition} ],
  anomalies:  [ <03 归档记录：id, stage:"plan", code, disposition, necessary_inputs, ...> ],
  decisions:  { "<doc_id>": {decision: body_reuse|compile|input_unavailable|suppressed, ocr, metadata} },
  ordering:   {stages:[mdcompare,plan,ocr,compile], compile_waits_for_ocr:false, second_approval_after_ocr:false},
  counts:     {documents, body_reuse, compile, ocr, metadata, suppressed, anomalies, external_calls:0},
  execution:  {plan_only:false, approval_required:false, automatic_extraction_allowed:true, full_document_fallback:true} }
```
判定规则：`previous_kind!=null` 且 body `status=REUSE`+`action=KEEP_EXISTING`→body_reuse；否则（新文档/正文增量/缺旧输入/REVIEW/UNKNOWN/NO_BODY/HOLD）当前 raw 可用时→compile（原因 `NEW_DOCUMENT_NOT_IN_GRAPH`/`BODY_DELTA_FULL_RECOMPILE`/`COMPARISON_UNCERTAIN_FULL_RECOMPILE`）。OCR 名单＝incoming 图片中未被证明 reusable 者（`processing_required` NEW/CHANGED ＋ 未验证 UNKNOWN），故正文复用但图片变化仍进 OCR；`ocr_blocking=false` 允许 OCR 不完整时用当前可用内容编译，OCR 后不生成第二套审批计划。元数据维护（name/logical_path/source_ref 变化或 renamed/moved 标签）纳入同一计划，不因正文复用遗漏。

### OCR 结果引用 schema（供 05 产出、06 消费）

基于现有 `mdcompare.ocr` 的 `prepared-jpeg-ocr-v1` 证据格式与 mdocr OCR 块标记。plan 的 `ocr[].resources[]` 是 05 的**输入**（待处理图片，resource_id=URL）。05 对每个 resource 产出结果并写入 canonical state 该文档 graph 记录的 `ocr` 字段，供下一批 mdcompare 验证复用：
```
ocr = {
  contract: "prepared-jpeg-ocr-v1",
  preprocessing: {name, version},          # 如 {"name":"mdocr","version":"1"}
  configuration: {engine, model, ...},     # 复用要求 old/new 完全一致
  images: [ {
    resource_id,                            # 与 plan ocr[].resources[].resource_id 一致（=URL）
    source_path,                            # 原图字节路径（须与 resource.path 字节相等）
    prepared_image,                         # 预处理后 JPEG（is_prepared_jpeg 校验）
    status: "success"|"failed"|"pending",
    artifact                                # OCR 文本产物路径
  } ]
}
```
`artifact` 为 mdocr Markdown 块：`<!-- mdocr:ocr-begin source="<prepared_image 名>" -->\n\n<ocr 正文>\n\n> Original image: [source](source)\n\n<!-- mdocr:ocr-end -->`。复用判定（`compare_ocr`）：resource UNCHANGED + old/new 均有 success 证据 + preprocessing/configuration 一致 + prepared JPEG 字节相等 + artifact 映射有效→`reusable`；否则进 `processing_required`。05 须把成功证据持久化到 graph 记录（本票 mdcompare 已从 `graph.get("ocr")` 读取 baseline 证据；incoming 证据由 05/06 在 OCR 成功后回填，下一批即可复用）。

### 执行阶段最终输入与缺失项字段（供 05/06 记录）

compile/ocr 项预留 `execution` 子对象，由执行阶段回填实际最终输入与缺失项（plan 侧不预填）：
```
execution = {
  final_inputs: [ {kind:"raw"|"ocr_artifact"|"prepared_image", resource_id?, path, source_version?} ],
  missing_items: [ {kind, resource_id?, code, reason} ],   # 如 OCR 失败/图片不可用
  completed_at, status
}
```
06 编译时以 `final_inputs` 记录真正使用的 raw 与 OCR 产物；OCR 不完整时按 `ocr_blocking=false` 用当前可用内容编译并把未就绪图片记入 `missing_items`，不阻断、不二次审批。metadata 维护执行侧（改名/搬动/来源引用落盘）由 06 按 `metadata[].tasks` 协调实现。

### 重试抑制与异常归档

消费 03：plan 用 `suppressing_anomalies(config, book_key)`（全阶段，pending/abandoned/done，stale 除外）按 `(document_id, version_stamp)` 抑制同版本已归档失败→`suppressed`（引用 anomaly_id，不重试、不重编译）；新版本 version_stamp 不同→正常处理。当前输入不可用→`archive_anomalies(context, [{stage:"plan", code, source_version, necessary_inputs}])` 持久化到 wash 之外（`sync-results/<book>/anomalies.json`），归档失败仅记日志、不破坏 corpus。

### 验证证据

- 聚焦：`tests/test_unified_plan.py` 8  passed（新文档/不确定→自动编译、正文复用保留知识并推进 graph、正文相同图片变化仍进 OCR、新文档图片进 OCR+compile 且含先后依赖、改名元数据维护、当前输入不可用归档异常不编译、同版本抑制/新版本正常）。
- `tests/test_unified_sync.py` + `test_unified_plan.py` + `test_knowledge_sync.py` + `test_sync_results.py`：72 passed。
- 全量：1101 passed，1 flaky（`test_user_interrupt_pauses_the_batch_with_exit_130`，全局 updater 锁/SIGINT 计时，单独跑通过；非本票回归）。基线 1094 保留。
- `ruff check` + `ruff format --check`（四个改动文件）：All checks passed / already formatted。
- 集成 tip 仍 `e95d37a`（本票基线），无需合入。

### 测试改写边界

仅改写因注册 mdcompare/plan 而必然失效的 5 条 runner 阶段边界断言（`test_unified_sync.py`：原假设 mdcompare 为下一个未注册阶段，现改为 ocr）：`test_unregistered_stage_fails_loudly_after_plan_ends`（原 ...after_capture_ends）、resume 复用冻结 capture、partial capture 交接、失败批仅重试失败阶段、status 命名旧批。**故障恢复/输入完整性意图全部保留**，仅把未注册阶段名 mdcompare→ocr。未改任何旧审批/质量断言（`test_knowledge_sync.py` 全绿，legacy 路径 08 收敛）；其余输入完整性/故障恢复回归未动。

### 未完成 / 协调

- OCR 证据回填与复用验证依赖 05 产出 `prepared-jpeg-ocr-v1` 证据并持久化到 graph 记录；本票已读取 baseline 证据、产出 OCR 工作名单与引用 schema， incoming 证据回填由 05/06 完成。
- metadata 维护执行侧、compile/ocr 的 `execution.final_inputs/missing_items` 回填由 06 按上述合同实现。
- legacy `--knowledge` 审批路径收敛属 08。

## Comments
