# 统一无人值守同步有界离线 UAT

日期：2026-11-12。验收对象是 `uus/08-acceptance` worktree 中可安装的 `dogpdkb sync` 统一入口及其五阶段批次行为，使用保留的真实本地 Markdown 与历史下载证据做**有界离线** UAT。这不是生产发布记录，也不冒称真实生产验收。

## 冻结输入与隐私

- 只读引用主 checkout（`/home/ubuntu/dev/dogpdkb`）的 gitignored 实物，复制 3 篇真实文档到临时 corpus：`578406922`、`585581197`、`585582020`（真实 book `340695`）。
- 历史下载证据取 `wash/fund-platform/knowledge-sync-20261110-233945/capture/` 的真实 `capture-checkpoint.json`、`pages/page-*.json` 与 `raw/` 原文；历史基线取 `graphify_flatten_ready/` 中对应 `-[id].md` 语料文件。
- 三篇真实原文正文与各自历史基线正文逐字相等（链接汇总类短文档），因此本 UAT 自然行使**真实内容上的正文复用**路径。
- 驱动脚本与全部临时工作区位于 `/tmp/uus08-uat/`（`uat.py`、`a-historical/`、`a2-no-update/`、`b-unified/`、`observations.json`）。`/tmp` 易失；公开文档不含正文、认证说明或凭证。
- 真实实物只读：运行前后对主 checkout 的 `.sync-state.json`、`graphify-out/graph.json` 与真实 `capture-checkpoint.json` 做 `size+mtime_ns` 守卫比对，结果完全一致（`real_artifacts_untouched: true`）。未修改、未重建真实语料或图谱，未恢复任何暂停的真实任务。

## 替身边界

- 语雀边界：本地 JSON-RPC `fake-proxy.py` 替身，服务上述真实字节与真实版本瞬时；不触达真实语雀网络。
- Graphify：确定性 `install_graphify_stub`（`GRAPHIFY_STUB_LOG` 记录 extract 调用，`GRAPHIFY_STUB_PARTIAL` 注入提取失败）。
- OCR：`sitecustomize` 注册的 OCR double（本 UAT 文档纯文本，无 OCR 工作量，阶段 skipped）。
- 全程无真实 Yuque/OCR/Graphify 调用、无网络、无付费、无 token 消耗、无生产同步。

## Phase A：历史下载证据复用与入口收敛

| 观察项 | 结果 |
| --- | --- |
| `discover --snapshot`（真实 checkpoint 子集 + 真实 raw） | `captured`，3 篇全部 `reused`，`failed=0`，零新导出 |
| canonical 下载版本 | 等于真实 checkpoint 的 UTC 版本（`2026-10-01T16:20:54+08:00` 等） |
| 复用 raw 字节 | 与真实历史下载逐字节相等 |
| discover 留下的未完成 claim | 统一 `sync` 被拒绝并提示 resume（两套独立流程不混用同一状态） |
| 新工作区首批 | `completed`，五阶段就位 |
| 无更新第二批 | `completed`，capture `reused=3 failed=0`，compile/ocr skipped，extract 次数不变 |
| `sync --knowledge` 兼容转发 | 弃用提示 + `completed`，run schema `unified-run-1.0`（与新入口一致） |

无更新批次中 `COMPARISON_PENDING` 会重新选中文档做廉价本地复核，但下载复用、不重编译、不重提取——符合规格收敛判据（不重复下载/OCR/提取），而非要求选择集合为空。

## Phase B：整批顺序、复用、暂停恢复、部分失败与清理

| 观察项 | 结果 |
| --- | --- |
| 首批整批顺序 | stderr 进度与 `run.json` 时间戳均证明 capture→mdcompare→plan→ocr→compile 整批顺序 |
| 首批路由 | 3 篇全部 `body_reuse`，compile 名单为空，extract 调用 `0` |
| 正文复用 canonical 引用 | 每篇 `graph.raw` 从 corpus 状态可解析到真实文件（**修复后**） |
| 成功版本 | 3 篇均推进到真实传入版本 |
| `status --json` | `completed`，`current_stage=null`，计数正确 |
| `--stop-after capture` | `paused`，capture `reused=3`，不启动下一阶段 |
| `--resume` | `completed`，冻结快照字节与 capture `ended_at` 不变，extract 不增加 |
| 无更新批次 | `completed`，capture `reused=3`，compile/ocr skipped，extract 不变 |
| 次日更新 + 注入提取失败 | `partial`，失败来源保留旧知识与旧成功版本，`active_run` 释放 |
| 异常归档 | 持久登记 `COMPILE_EXTRACTION_FAILED`（身份复用 book+doc+version） |
| 同版本下一批 | `completed`，该版本被抑制不重试、不重提取，pending 移交仍持久 |
| `anomalies --resolve … abandoned` | 处置可核对；放弃仅作用于该版本，下一批仍 `completed` 不重提取 |
| wash 清理 | 仅保留最近一次已结束 run，持久 `runs/`、`anomalies.json`、`migrated/` 存活 |
| 清理后引用迁移 | 被清理批次的复用 raw 已迁移到 `<results>/migrated/<run>/…` 且仍可解析 |

编译失败异常的 `necessary_inputs` 为空（编译失败不另行归档必要输入，与采集异常不同）；异常记录本身与批次归档持久存活，清理后仍可读取。

## 发现并修复的缺陷

UAT 在真实内容上暴露一个跨批次正文复用缺陷：`stages/plan.py::_update_state` 把 mdcompare 报告相对的 raw 引用（`inputs/<book>-<doc>.md`）原样写入 canonical `corpus.raw` 与 `graph.raw`，而 canonical 状态以 corpus 为基准解析，导致引用悬空。下一批次因此判 `PREVIOUS_INPUT_MISSING` 并对已验证正文复用的来源**重新编译**，违反规格 Testing Decisions“明确定义的正文复用不重复编译”。

最小修复：在 plan 写状态前以 mdcompare 报告为 owner 解析 raw，再按 compile 路径同样的约定相对 canonical 状态重写（`_resolve_raw` → `_relative_raw`），使跨批次复用收敛、wash 迁移可持久化。新增 CLI 黑盒验收测试 `test_body_reuse_survives_the_next_batch_without_recompiling`（修复前红、修复后绿），并在本 UAT 的真实内容上复核引用可解析与清理后迁移。

## 分别报告（真实服务 / token / 安装交付 / 生产同步）

- 真实语雀/OCR/Graphify 服务调用：**未执行**（替身）。
- token 消耗：**未执行**（无付费调用；Graphify stub 不计费）。
- 安装交付：以隔离安装 `make smoke` 作为**授权**的安装交付检查（构建 wheel/sdist、隔离 venv 安装、统一五阶段 sync/复用、canonical 状态/UTC+8/锁、doctor、Web、images、托管升级/卸载）。
- 生产同步：**未执行、未授权**；不恢复暂停的真实任务，不做生产全量回填。

## 未验证项

- 真实 OCR/Graphify 的语义质量与真实语雀网络行为（替身不覆盖）。
- 生产全量同步、真实暂停旧 run 的恢复（规格不授权；旧实现暂停 run 不默认新实现可恢复）。
- 判据为可观察阶段行为/产物/状态/重复调用次数/恢复结果，**不**以某次模型节点数或语义无损为判据。

## 复现

驱动脚本 `/tmp/uus08-uat/uat.py`（只读引用主 checkout 实物，临时工作区 + 替身），运行后写 `/tmp/uus08-uat/observations.json`。`/tmp` 易失；长期证据以本记录与票 08 Answer 的实测计数为准。
