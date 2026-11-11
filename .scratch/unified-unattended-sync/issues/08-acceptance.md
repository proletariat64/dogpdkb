# 08: 验收：回归收敛、离线 UAT、门禁与安装 smoke

Type: task
Status: resolved
Blocked by: 01, 02, 03, 04, 05, 06, 07

## Context

- [规格](../spec.md) Testing Decisions 全节；[地图](../map.md)。
- 先读 `/tmp/unified-unattended-sync-notes/00-base-context.md`；补充笔记 01/02/03（测试缝、Graphify shim、安装 smoke）。各票 Answer 为公开合同。
- 最高验收入口：可安装 dogpdkb CLI 黑盒，临时 corpus + 真实文件 + 外部接口替身；不增只为测试的新接口，不以私有脚本为产品入口。

## Acceptance and ownership

1. **入口收敛**：移除旧 sync 执行器作为独立默认（01 过渡的旧 flag 路径收敛为带弃用提示的兼容转发或删除）；`sync --knowledge` 如保留与新入口一致；不保留另一套默认规则/旧执行器。独立 mdcompare/mdocr/discover/apply/update-graph/publish-web 工具按规格保留各自用途。
2. **断言改写边界**：只改写被规格明确取代的人工审批/语义质量阻断断言；其余输入完整性与故障恢复回归继续保留并绿。
3. **五阶段整批顺序**：全部采集结束才比较、全部比较结束才规划、全部 OCR 结束才编译；覆盖零工作量/部分失败/无更新批次。stop-after 不启动下一阶段；resume 不重复有效工作；用户锁拒绝并发。
4. **复用/路由/OCR/编译/状态/异常/清理**：按规格 Testing Decisions 逐条验证（同版本零重复下载、跨批次/UAT 复用、富化不冒充 raw、重叠标签一次、不回填；正文复用不重编译、REVIEW/UNKNOWN 自动完整编译、输入不可用只移交；OCR 缓存/无效/单图/token/额度/超时/预算；完整来源/部分发布/旧来源保留/无效图不推进/故障恢复不重复计费；stage/run 状态/status JSON/暂停崩溃识别/日志重定向/凭证不入输出/partial 可结束；同版本异常不每日重试/新版本正常/处置可核对/OCR 后补登记编译；清理只处理已结束批次/保留最新与暂停/缓存正式产物/清理后异常可读）。
5. **有界离线 UAT**：用保留的真实本地 Markdown 与历史下载证据做有界离线 UAT（只读引用主 checkout 的 gitignored 实物，不修改）；真实服务调用/token 消耗/安装交付/生产同步分别报告。规格不授权恢复暂停任务或新真实 OCR/Graphify 调用。
6. **门禁与 smoke**：完整 `make check`（doc-guard + ruff + pytest）与隔离安装 `make smoke` 绿。判据是可观察阶段行为/产物/状态/重复调用次数/恢复结果，不是某次模型节点数或语义无损。

主要拥有：验收测试、入口收敛改动、UAT 报告、docs 验收记录。跨模块修复以最小必要为限。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/08-acceptance`）；开工前确认基于集成 tip（含 01–07），否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd` 补齐缺失验收测试。临时 corpus + 替身；无真实服务/付费/生产。完成后合入最新集成 tip 再跑完整 make check + make smoke，提交，Status 改 resolved，Answer 列实测计数与日志路径。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Answer

### 实现

入口收敛 + 缺口验收测试 + 安装 smoke 收敛 + 有界离线 UAT（并在真实内容上发现/最小修复一个跨批正文复用缺陷）。提交：`2dfacba`（入口收敛）→ `0724f8a`（整批顺序+无更新收敛 CLI 测试）→ `f03a133`（install smoke 收敛）→ `9fa3b2f`（UAT 发现的 plan body-reuse 引用修复）→ `033980b`（跨批正文复用收敛回归测试）→ `afc6a39`（UAT 记录+README 指针）。新增 `tests/test_unified_acceptance.py`、`docs/unattended-sync-uat.md`。

### 入口收敛选择（验收 1）

`dogpdkb sync` 收敛为**唯一统一五阶段入口**，旧执行器不再是第二默认：

- `workflows/sync_knowledge_base.py::main` 现在**总是**走 `unified_sync.main`。检测到 legacy flag（`--knowledge`/`--cutoff` 等，`unified_sync.legacy_flags`）时打印弃用提示，再经 `unified_sync.unified_arguments` 取兼容子集（仅保留 `--config`/`--book`/`--stop-after`/`--resume <run>` 及其值；丢弃被取代的执行器 flag 与无值布尔 `--resume`）转发入统一 runner。`deprecation_notice` 明示"旧 sync 执行器已移除，本次调用转发到统一 runner，legacy-only flag 被忽略"。
- `sync --knowledge` 与新入口**一致**：跑同一五阶段批次、产 `unified-run-1.0`，无人工审批/语义质量日常门禁（UAT Phase A 实测 `--knowledge` 兼容转发→completed/paused、schema unified-run-1.0）。
- 旧审批/管线执行器保留为 `knowledge_sync.py`（`run_planner`，APPROVAL_SCHEMA knowledge-approvals-2.0）与 `sync_knowledge_base.py::legacy_main`，**仅迁移/回归用，不从 `dogpdkb sync` 可达**；`test_knowledge_sync.py` 直接 `python -m dogpdkb.knowledge_sync` 行使，使其输入完整性/故障恢复回归继续被锻炼。
- 独立工具 `discover`/`apply`/`update-graph`/`publish-web`（及 mdcompare/mdocr）按规格保留各自用途，未改其默认。

### 断言改写边界（验收 2）

只改写被规格取代的人工审批/语义质量阻断与"legacy flag 到达旧执行器"断言；输入完整性/故障恢复回归全保留：

- `test_unified_sync.py`：`test_legacy_flags_still_reach_the_old_executors_with_a_deprecation_notice` → 改名 `test_legacy_flags_forward_into_the_unified_runner_with_a_deprecation_notice`，断言 legacy flag 转发入统一入口（returncode 0、弃用提示、**无** `knowledge sync: INVALID_ARGUMENTS`、**无** `sync_knowledge_base.py` 恢复命令、run schema `unified-run-1.0`）。
- `test_knowledge_sync.py`：`sync()` helper 改调 `python -m dogpdkb.knowledge_sync`（保留的弃用兼容执行器）直接行使；**输入完整性断言一字未改**（注释明示）。其余 test_pipeline/test_config/test_knowledge_execute/test_sync_progress_cli 仅入口/被取代审批断言的最小调整，恢复/复用意图保留。

### UAT 发现并最小修复的缺陷

有界离线 UAT 在真实内容上暴露跨批正文复用缺陷：`stages/plan.py::_update_state` 把 mdcompare 报告相对的 raw 引用（`inputs/<book>-<doc>.md`）原样写入 canonical `corpus.raw`/`graph.raw`，而 canonical 以 corpus 为基准解析→引用悬空→下一批误判 `PREVIOUS_INPUT_MISSING` 并对已验证正文复用来源**重新编译**，违反 Testing Decisions"明确定义的正文复用不重复编译"。最小修复（`9fa3b2f`，plan.py +14/-4）：写状态前以 mdcompare 报告为 owner 解析 raw，再按 compile 路径同约定相对 canonical 重写（`_resolve_raw`→`_relative_raw`），使跨批复用收敛、wash 迁移可持久化。新增 CLI 黑盒回归 `test_body_reuse_survives_the_next_batch_without_recompiling`（修复前红、修复后绿）。

### Testing Decisions 覆盖映射（验收 3–4，规格 143–155 行）

1. 最高验收入口（可安装 CLI 黑盒+临时 corpus+真实文件+替身，无测试-only 接口）→ 全部 unified 测试 + `test_unified_acceptance.py`。
2. 复用既有检查点/下载复用/比较/OCR/编译候选/发布恢复/install smoke 模式，只改写被取代断言 → `test_capture_reuse`/`test_mdcompare_ocr`/`test_apply_recovery`/`smoke_install.py`；边界见上。
3. 五阶段整批顺序+零工作量/部分失败/无更新 → `test_unified_acceptance.py::test_full_batch_runs_all_five_stages_in_whole_batch_order`、`::test_no_update_second_batch_completes_by_reusing_recorded_work`；`test_unified_sync` 零工作量/partial。
4. 唯一 sync 入口/knowledge 兼容一致/stop-after 不启动下一阶段/resume 不重复/用户锁拒并发 → `test_legacy_flags_forward_into_the_unified_runner…`、`test_unified_sync` stop-after/resume/锁测试。
5. 同版本零重复下载/跨批与 UAT 复用/缺失与变化重下载/历史富化不冒充 raw/重叠标签一次/不回填 → `test_capture_reuse`、`test_unified_plan`、UAT Phase A。
6. 正文复用不重编译/新文档与 REVIEW/UNKNOWN/缺旧输入自动完整编译/输入不可用只移交/图片变化不被正文复用吞掉 → `test_unified_plan`、`test_body_reuse_survives_the_next_batch_without_recompiling`、UAT Phase B。
7. OCR 缓存/结果变化/无效/单图/token/额度/超时/预算，均结束 OCR 继续编译并保留正确待处理数 → `test_unified_ocr`（11 条）。
8. 完整来源/部分发布旧来源保留/无效图或写入失败不推进/提交-状态故障恢复不重计费 → `test_unified_compile`、`test_knowledge_execute`、`test_apply_recovery`。
9. stage/run 状态/status JSON/暂停崩溃识别/日志重定向/第三方诊断/凭证不入输出/partial 可结束允许下次 cron → `test_unified_sync`、`test_sync_results`、`test_platform`。
10. 同版本异常不每日重试/新版本正常/重复扫描与处置可核对/OCR 后补登记编译不伪造成功 → `test_sync_results`、`test_unified_ocr`、`test_unified_compile`。
11. 清理只处理已结束批次/保留最新与暂停/真实缓存与正式产物/清理后异常可读/旧引用迁移/归档失败不丢输入 → `test_unified_wash`（10 条）。
12. 有界离线 UAT（真实本地 Markdown+历史下载证据），真实服务/token/安装交付/生产同步分别报告 → `docs/unattended-sync-uat.md`。
13. 完整门禁+隔离安装 smoke，判据为可观察行为/产物/状态/重复次数/恢复而非节点数/语义无损 → `make check` + `make smoke`（见下）。

### 有界离线 UAT（验收 5）

`docs/unattended-sync-uat.md`：只读引用主 checkout gitignored 实物，复制 3 篇真实文档（`578406922`/`585581197`/`585582020`，真实 book `340695`）+ 历史 `capture-checkpoint.json`/`pages`/`raw` 证据到临时 corpus；语雀 fake-proxy、Graphify 确定性 stub、OCR double 替身。运行前后对真实 `.sync-state.json`/`graph.json`/`capture-checkpoint.json` 做 size+mtime_ns 守卫，`real_artifacts_untouched: true`（未修改/未重建/未恢复暂停真实任务）。Phase A 历史下载复用+入口收敛、Phase B 整批顺序/正文复用/暂停恢复/部分失败/异常归档/同版本抑制/处置/wash 清理与引用迁移均实测通过。**分别报告**：真实语雀/OCR/Graphify 调用未执行、token 未消耗、生产同步未执行未授权；安装交付以隔离 `make smoke` 为授权检查。未验证项：真实 OCR/Graphify 语义质量与真实语雀网络、生产全量同步、真实暂停旧 run 恢复（规格不授权）。替身验收不冒称真实生产验收。

### 验证证据（验收 6）

- 全量 `make check`（前台）：doc-guard PASS（86 documents）、`ruff check` All checks passed、`ruff format --check` 222 files already formatted、pytest **1131 passed**（230.40s，基线 1128（07 合入后）+ 本票新增 3 条验收测试），exit 0，无 flaky 失败（`test_user_interrupt_pauses_the_batch_with_exit_130` 本轮通过）。
- 隔离安装 `make smoke`：**PASS**（wheel/sdist 构建、隔离 venv 安装、CLI、mdcompare、knowledge snapshot/discover、unified 五阶段 sync/reuse、canonical state/UTC+8/锁、doctor、Web、images、托管升级/卸载），exit 0。
- 聚焦 `test_unified_acceptance + test_unified_sync + test_knowledge_sync + test_pipeline`：66 passed。
- 集成 tip：开工基于 `f502580`（含 01–07）；收尾前合入 `cb27441`（merge `25cc425`，仅 map.md，无冲突）。UAT 驱动脚本 `/tmp/uus08-uat/uat.py`（/tmp 易失，长期证据以本记录与上述实测计数为准）。

### 未完成 / 协调

- 无未完成项；6 条验收全部实现并验证。09（最终审查修复）消费本集成分支：入口已收敛、旧执行器仅迁移/回归保留、跨批正文复用缺陷已修。
- 真实服务/生产同步/真实暂停 run 恢复仍未授权（规格 Out of Scope），不在本票冒称验收。

## Comments
