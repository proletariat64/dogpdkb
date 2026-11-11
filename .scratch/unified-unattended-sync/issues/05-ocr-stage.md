# 05: OCR 尽力完成阶段

Type: task
Status: ready-for-agent
Blocked by: 04

## Context

- [规格](../spec.md) OCR 尽力完成节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 `03-ocr-compare-config-tests.md`（mdocr 缓存身份/复用、超时、token/额度错误、结果格式）与 `02-knowledge-modules.md`。工作树代码为准。
- 04 Answer：plan 的 OCR 名单与 OCR 结果引用 schema（基于现有 mdocr 结果格式）。03 Answer：异常归档 API、批次共享原因、重试抑制、OCR 后补合同。
- 现有 mdocr 图片处理/缓存能力沿用，不重写识别。
- 关键现状（见 00-base-context / 笔记 03）：**当前没有 OCR 结果缓存**——现有复用仅靠同 markdown 内保留的 `<!-- mdocr:ocr-begin -->` 块、内容命名 assets、文件名 checkpoint。本票需新建持久 OCR 缓存（独立于待清理 run），复用判定走 mdcompare `prepared-jpeg-ocr-v1` 证明合同（preprocessing+configuration 规范化相等、原图↔ prepared 字节对应、prepared-JPEG 字节相等、规范非空产物），不新造指纹。PaddleClient：token 仅来自 env PADDLEOCR_TOKEN，60s/调用、3 次重试、POST 提交不重试（计费）、900s job 截止。auth/quota 分类、全批停止、阶段预算、成功/缓存/失败/未尝试计数均不存在，需新建。**默认 sync 现在 OCR 失败即阻断发布（apply_sync_updates.py:610-626），与规格尽力完成相反，须移除该阻断。** 配置无 OCR/cache 键：新增 TOML 键（如 [paths].ocr_cache、[ocr] 段）经 current_config() 读取并更新 snapshot()/模板/README，不新增任意环境变量（PADDLEOCR_TOKEN 保持 env）。

## Acceptance and ownership

1. **只处理本批计划图片**：注册 ocr handler；复用 mdocr 处理与缓存。同一已验证图片内容+兼容处理配置优先复用有效成功结果，不因重复同步主动重 OCR；URL 相同不足以证明图片未变。图片变化或无有效结果才识别。
2. **缓存保护**：失败/空白/不可解析的新结果不覆盖已有有效结果；不同图片的旧结果不当新图片结果用。成功结果经现有格式/产物检查后持久保存（长期缓存独立于待清理 run），不做模型语义质量审批。
3. **失败隔离与全批停止**：单图失败记录后继续其他图片；认证不可用/额度耗尽等全批共享原因停止本批剩余 OCR（记批次原因+枚举受影响图片，交 03 归档）；未知错误不无限重试。
4. **有限等待与预算**：外部调用有限超时+重试；OCR 阶段有限总时间预算，可运行默认值（走现有配置体系）。预算耗尽保留已完成结果、未完成项归档、进入 compile。
5. **计数**：区分成功/缓存复用/失败/未尝试；额度不足未执行不计成功。
6. **不阻断编译**：OCR 补齐非完成必要条件；产出 04 定义的 OCR 结果引用供 compile 消费；未识别图片保留引用。后补成功更新缓存+真实完成+处置，改变已发布编译输入时登记后续编译（03 合同）。
7. **重试抑制**：同版本已归档 OCR 失败不每日重试；新版本正常处理。

主要拥有：ocr 阶段 handler、mdocr 必要扩展（预算/全批停止/计数）、相关测试。避免改 plan（04）、compile（06）、比较器。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/05-ocr-stage`）；开工前确认基于集成 tip（含 04），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + OCR HTTP 替身；无真实 OCR/token/付费调用。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments
