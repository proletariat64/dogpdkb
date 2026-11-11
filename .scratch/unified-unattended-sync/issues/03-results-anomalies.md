# 03: 持久批次结果、异常登记与处置合同

Type: task
Status: ready-for-agent
Blocked by: 01

## Context

- [规格](../spec.md) 持久结果、异常移交与 wash 清理节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 01/02/03。01 的 Answer 公布 run 目录布局与状态 schema——本票消费它。
- 现有身份/版本体系：canonical state（book key + doc ID + version{field,value,utc}）、snapshot exports、checkpoint。异常身份复用这些，不新增内容指纹机制。

## Acceptance and ownership

1. **持久结构化批次结果**：每批结束前持久保存（独立于待清理 wash run 目录，位置走现有配置体系）；含 run、book/doc ID、来源版本、阶段、计数、结果引用。历史结果可重复扫描。
2. **异常登记**：异常含 run、知识库/文档 ID、来源版本、阶段、原因、图片身份及必要输入引用；必要输入持久化（wash 清理后仍可读取）。共享 token/额度错误记录为批次原因并能枚举受影响图片。稳定身份区分待处理/完成/放弃/失效。
3. **重试抑制合同**：同一来源版本已归档失败不在每日 sync 无限重复尝试；尝试/移交状态与真实成功状态分开保存，不用虚假成功避免重试。新远端版本正常参加日常同步；对旧版本的放弃只作用该版本，不永久封禁来源。未变化远端的历史 OCR 待补交事后 agent，不每日自动重试。
4. **处置入口**：供事后 agent 扫描结果、读取必要输入、对指定版本异常记录完成/放弃/失效（新只读+处置子命令，如 `dogpdkb anomalies`；不是第二套顶层阶段命令）。重复扫描不重复执行。在 Answer 公布结果/异常 schema、处置 CLI 合同。
5. **OCR 后补合同**：事后处理成功后必须更新对应缓存、真实完成状态与处置结果；OCR 补齐若改变已发布编译输入，显式登记后续编译工作（合同在此定义，05/06 执行），不能声称仅写 OCR 缓存即完成知识更新。
6. **阶段归档 API**：提供各阶段调用的归档接口；本票完成 capture 阶段（01 已接线）的失败归档集成作为首个消费者；04/05/06 各自阶段集成时复用。归档失败不丢输入、不破坏有效知识库。

主要拥有：新持久结果/异常模块（如 sync_results.py）、处置子命令、cli.py 最小分发、capture 归档集成、新测试。避免改 runner 状态机核心（01）、plan/ocr/compile handler（04/05/06）。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/03-results-anomalies`）；开工前确认基于集成 tip（含 01），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 替身；无真实服务/付费调用。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments