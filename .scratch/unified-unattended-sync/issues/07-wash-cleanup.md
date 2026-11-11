# 07: wash 清理、历史迁移与部署文档

Type: task
Status: ready-for-agent
Blocked by: 03, 05, 06

## Context

- [规格](../spec.md) 持久结果、异常移交与 wash 清理节 + 查询、日志与部署方式节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 01（wash 布局）。03 Answer：持久结果/异常输入位置。01 Answer：run 目录布局。05/06 Answer：长期缓存/正式产物位置。
- wash 是批次工作空间（分页/阶段报告/plan/临时编译文件），不能是长期原文/成功 OCR/正式语料/图谱/异常恢复输入的唯一位置。

## Acceptance and ownership

1. **自动清理**：每次 completed 或 partial 结束后清理更早的已结束工作目录，只保留最近一次已结束批次 + 暂停/未完成批次。持久历史结果、异常输入、长期缓存不随 wash 清理。
2. **清理安全**：清理前先完成结果归档与必要输入持久化；确认状态/缓存/异常记录不再引用待删目录中的唯一文件。清理失败记录结果、不破坏有效知识库，下次结束可再清理。
3. **历史 wash 首次整理**：盘点引用并迁移必要产物，不直接删整目录。长期缓存与异常归档本次不引入自动淘汰策略。旧实现暂停的 run 不默认新实现已验证可恢复（迁移标注，恢复需校验）。
4. **部署文档**：crontab 明确程序/配置/日志位置、运行环境提供既有认证；shell 合并追加 stdout/stderr 到普通日志；系统 logrotate 轮转。工具不内置日志轮转守护、不自动安装 cron。文档落 docs/（如 docs/unattended-sync.md），含可复制 crontab/logrotate 示例。
5. **清理后异常仍可读**：历史异常必要输入在清理后仍可读取（与 03 归档一致）；旧状态引用迁移正确。

主要拥有：清理模块/runner 收尾钩子、历史迁移工具、部署文档、相关测试。避免改阶段 handler 核心逻辑。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/07-wash-cleanup`）；开工前确认基于集成 tip（含 03/05/06），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 替身；清理测试用构造的 wash 目录，不删真实私有实物/生产语料。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments
