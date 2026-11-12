# 02: capture 下载复用与版本证据

Type: task
Status: resolved
Blocked by: none

## Context

- [规格](../spec.md) capture 与下载复用节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 `02-knowledge-modules.md`（capture/checkpoint 合同、历史导入来源、exact-dict-vs-instant 版本匹配陷阱）。工作树代码为准（knowledge_capture.py 已含 state-baseline 的 canonical 状态/精确时间改动）。
- 现有能力：knowledge-capture-1.0 snapshot、knowledge-capture-checkpoint-1.0（按 book+列表版本复用成功 raw）、标签去重、近期 cutoff、pending 续跑、canonical state 下载选择。本票在其上补齐规格要求的复用证据链，不重写采集。

## Acceptance and ownership

1. **同版本有效原文零重复下载**：同知识库+同文档 ID+相同远端版本且原文文件有效时复用；有效性检查复用现有保证或直接比较，不新增通用指纹系统。缺失/失效必需文件重新获取。
2. **历史下载记录导入**：可导入历史同步或 UAT 中具有相同证据（知识库+文档 ID+版本+有效文件）的下载记录；不仅凭文件名、日期或文件存在判断同版本。导入记录与本次 checkpoint 复用走同一证据判定。在 Answer 公布导入入口/合同（CLI 参数或配置，沿用现有配置体系）。
3. **历史富化不冒充 raw**：无法证明版本的历史文件可继续作为已有基线（historical_enriched），但不能标成已验证当前 raw；不得把历史 OCR 富化 Markdown 标成原始导出。
4. **版本区别保留**：观察版本、下载版本、成功发布版本分开；成功下载不提前声称图谱更新。列表/详情版本分别保存（沿用）。
5. **既有边界不回归**：重叠标签只下载一次；不回填历史缺失；完整清单失败=阶段失败，单篇导出失败归档不阻断；缺席标签不推断删除。
6. snapshot 合同保持向后兼容（可加字段）；01 的 capture handler 按现有 documents/exports/raw 引用消费，不破坏。

主要拥有：knowledge_capture.py、必要的 get_sync_updates.py 最小分发、tests/test_knowledge_capture.py 及新增复用测试。避免改 cli.py/unified runner（01）、knowledge_plan/execute（04/06）。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/02-capture-reuse`）；开工前确认基于集成 tip，否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd`：从一条失败的 CLI 黑盒测试开始，每轮记录 red/green。临时 corpus + 本地 listing/details 替身；不访问真实语雀，不运行 OCR/Graphify，不改生产语料/图谱/基线。完成后合入最新集成 tip 再跑聚焦测试 + ruff check + format --check，提交，Status 改 resolved，Answer 列公开合同与验证证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Comments

## Answer

分支 `uus/02-capture-reuse`，实现提交 `e15bd4f`（已合入集成 tip `19a6b1f`），worktree `/home/ubuntu/dev/dogpdkb/.qoder/worktrees/uus-02-capture-reuse`。在现有 `knowledge_capture.py` 上扩展，未重写采集；未触碰 cli.py/unified runner 与 knowledge_plan/execute。

### 公开合同

**导入入口**（沿用现有 discover 配置体系，`get_sync_updates.py` 仅新增参数分发）：

```
dogpdkb discover --snapshot <snapshot.json> --import-downloads <记录.json> [<记录2.json> ...]
```

- 接受两种格式（按内容识别；book 身份不符或不支持的格式 → CaptureError 阶段失败）：
  - knowledge-capture-1.0 snapshot：book 证据为 `book.book_id`；记录须 `status=captured`、raw stage 为 `raw_export`、列表/详情版本齐全。复用 adapter `imported_capture_snapshot`，保留原 `captured_at`。
  - 旧版 export-progress.json：book 证据为 `configuration.source_url`（去尾斜杠比较）；记录须 `status=exported`。版本证据优先取结构化 `source_version`/`listing_version`；基线前旧记录的 `remote_content_updated_at` 按详情 `content_updated_at` 处理。复用 adapter `imported_export_progress`。
- 复用优先级：canonical 状态下载 → checkpoint → 导入 → 新鲜导出。三条复用路径共用同一证据判定：
  1. 同 book + 同文档 ID + 版本**时刻相等**（parse_timestamp instant 比较，非精确字典比较；listing 版本可缺省，此时仅比详情）。
  2. raw 有效性为直接检查：常规文件、UTF-8 可读、不含富化标记（`<!-- mdocr:ocr-begin`、`这是一张图片，ocr内容为`）。未新增指纹系统。
  3. 逐条拒绝原因记入 snapshot：invalid_document_id / invalid_raw_file / version_missing / version_invalid / raw_missing / raw_invalid / raw_enriched / duplicate_document。文件名、日期或文件存在本身不构成证据。
- 导入物化：字节经现有 `_save_raw` 复制进 staging/raw（原子写、0600、绝不覆盖内容不同的有效 raw；冲突回退正常导出，失败走 export_failed 归档）。同时合成 checkpoint 行，后续 `--pending-state` 续跑无需再带导入参数。
- snapshot 可加字段（knowledge-capture-1.0 向后兼容，既有 documents/exports/raw 引用不变，01 handler 照常消费）：
  - 顶层 `imports: [{path, format, candidates(整数), rejected: [{id, reason}], reused: [doc_id...]}]`，恒存在（未启用导入为空数组）。
  - `exports[].imported_from`（复用记录来源路径）；checkpoint 行 `imported_from` 与可选 `listing_version`。
- 历史富化文件仍留在 historical_enriched 基线，导入不触碰基线、不冒充已验证当前 raw；观察/下载/发布版本保持分离，成功下载不声称图谱更新。

### 边界不回归

新增测试覆盖：重叠标签只下载一次、不回填未选中的历史文档、两种格式 book 不符均拒绝、损坏 checkpoint/canonical raw 重新获取、版本时刻匹配（同一时刻不同时区表示可复用）、基线前旧记录证据、导入后无参续跑、raw 冲突永不覆盖、畸形版本记录、自导入路径冲突（REPORT_INPUT_COLLISION）。完整清单失败=阶段失败、单篇导出失败归档、缺席不推断删除由既有 test_knowledge_capture.py 用例继续把关。

### 验证证据

- TDD：每个垂直切片从失败的 CLI 黑盒测试开始（red），实现后转 green；临时 corpus + 本地 listing/details 替身，全程无真实语雀/OCR/Graphify/网络调用，未写生产语料/图谱/基线。
- 新增 `tests/test_capture_reuse.py` 16 例；聚焦 `pytest tests/test_knowledge_capture.py tests/test_capture_reuse.py -q` → **72 passed**。
- 扩展回归 `pytest tests/test_knowledge_capture.py tests/test_capture_reuse.py tests/test_knowledge_sync.py tests/test_knowledge_execute.py tests/test_sync_progress_cli.py tests/test_sync_state_cli.py -q` → **145 passed**。
- 全量 `.venv/bin/python -m pytest -q` → **1059 passed**（195s）。
- `.venv/bin/python -m ruff check .` → All checks passed；`.venv/bin/python -m ruff format --check .` → 199 files already formatted。
- 合入集成 tip `19a6b1f` 后重跑聚焦 72 passed + ruff 双门，干净。
- 文档：`docs/daily-knowledge-sync.md` 新增「下载复用与历史记录导入」小节。
- 备注：doc-guard 对 .scratch 其他票文件的行尾换行告警为集成分支既有问题（改动前 `git diff unified-unattended-sync -- .scratch/` 为空），本票仅修复自身文件行尾。
