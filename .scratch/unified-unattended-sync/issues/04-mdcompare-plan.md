# 04: mdcompare 整批阶段与自动 plan

Type: task
Status: ready-for-agent
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

## Comments