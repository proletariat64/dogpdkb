# 09: 最终双轴审查修复

Type: task
Status: resolved
Blocked by: 08

## Context

- [规格](../spec.md)；[地图](../map.md)。08 完成后由 orchestrator 调用 Skill `code-review`（Standards + Spec 双轴）对集成分支审查。
- 本票关闭审查发现的全部问题；审查记录落 docs/（如 docs/unified-unattended-sync-review.md）。

## Acceptance and ownership

1. 修复 code-review 提出的全部 Standards 与 Spec 问题，每项有命名回归测试。
2. 不扩大范围：只修审查发现，不新增功能。
3. 修复合入后完整 make check + make smoke 绿；Answer 列每项发现→修复→回归证据。

主要拥有：审查修复改动、回归测试、审查记录文档。

## Working rules

在独立 worktree 基于集成分支 `unified-unattended-sync` 工作（分支 `uus/09-review-fixes`）；开工前确认基于集成 tip，否则 reset 到它（仅限本票新分支）。`make bootstrap`。调用 Skill `tdd` 为每项修复先写失败回归。完成后合入最新集成 tip 再跑完整 make check + make smoke，提交，Status 改 resolved，Answer 列证据。失败/部分实现保持 claimed。不得 reset/clean 共享状态或删私有实物。

## Answer

### 实现

双轴 code-review（基线 `f09f45d`=origin/master，91 提交/60 文件）全部发现处置完毕：**9 项修复**（含 7 条命名回归）、**2 项 won't-fix**（H1/H2，证据裁决）、**1 项信息性延后**（Spec Finding 2）、**10 项纯重构 judgement 延后**（守"不扩范围"）。审查记录 `docs/unified-unattended-sync-review.md`（逐项处置+回归证据+裁决理由）。

提交链：`cc6752a`（F1 Spec-1）→ `d4c6e4f`（F2 J1）→ `1cd94d8`（F3 J14）→ `ef56e43`（F4 J13）→ `742221a`（F5 J16 + F9 J11）→ `2ccead6`（F8 J5）→ `9a4e2c2`（F7 J9）→ `f33525f`（F6 J10）→ `56482d3`（合入集成 tip aec64b3）→ `b90d635`（审查记录）→ 本结案提交。implementer 触顶 150 turn 前已落全部 9 项修复（8 提交，树干净）；orchestrator 核验关键改动 sound 后收尾（合入 tip/全量门禁/审查记录/结案）。

### 逐项发现 → 处置 → 回归证据

**修复（TIER 1–3，每项命名回归）：**

- **F1 = Spec Finding 1（Medium，最重）**：body-reuse 来源遇图片变化时内联 OCR 成功永不进 compile，新图片文本不进图谱（违 spec:94/136/148；08 UAT 纯文本 OCR skipped 未行使）。修复 `cc6752a`：`stages/ocr.py` 累计 `changed_inputs`（仅 success 新识别，不含 reused），对 body-reuse 来源调 `register_compile_followup` 复用 06 既有机制（无新机制/无二次审批计划），compile `_pending_followup_items`/`_consume_followups` 消费。回归 `test_body_reuse_changed_image_successful_ocr_is_compiled_and_publishes` + 对照 `test_body_reuse_reused_image_is_not_recompiled`（修复前红→绿）。
- **F2 = J1（fd 泄漏）**：`instance_lock.lock_is_held` 三返回路径均不 close fd（status watch 耗尽）。修复 `d4c6e4f` try/finally `os.close(fd)`，语义不变。回归 `test_lock_is_held_does_not_leak_descriptors`。
- **F3 = J14（body-reuse 抹掉 graph 证据）**：`plan.py:294` 整体替换 graph 记录丢 ocr/body_scope。修复 `1cd94d8` 合并保留既有键。回归 `test_body_reuse_plan_preserves_prior_ocr_evidence_and_body_scope`。
- **F4 = J13（crash-resume 守卫，验证为真）**：两审查员分歧；implementer 实证硬崩溃在 artifact 落盘后/记录完成前→阶段仍 running→resume 被"output already exists"永久阻断（违 spec:150）。修复 `ef56e43` 防覆盖守卫限首次尝试（`attempts<=1`），resume 原子重建，symlink 拒绝无条件保留。回归 `test_resume_recovers_a_crash_after_the_compile_artifact_lands` + `test_resume_recovers_a_crash_between_plan_artifact_and_stage_record`（os._exit(137) 注入崩溃→resume 恢复）。
- **F5 = J16（status 脱敏）**：`sync_status.py:154/170` 经 `safe_diagnostic` 脱敏（`742221a`）。回归 `test_status_redacts_credential_forms_in_its_error_output`。
- **F6 = J10**：剥离生产代码票/项脚手架注释（`f33525f`），保留 WHY 注释，无行为变化。
- **F7 = J9**：移除核验未用的参数/字段（`9a4e2c2`）。
- **F8 = J5**：导入规范 `PLAN_SCHEMA` 替代重定义字面量（`2ccead6`），无环。
- **F9 = J11**：`report(view)` 参数改名消除对 `view()` 遮蔽（`742221a`），无行为变化。

**Won't-fix（证据裁决，不破坏）：**

- **H1（Standards 最重，"保留旧执行器/test-only 接口"）**：产品入口 `sync_knowledge_base.main` 总转发统一 runner（spec:70 核心—无第二默认—满足）；`knowledge_sync.__main__`/`legacy_main` 仅 `python -m`/测试可达，**不从 `dogpdkb sync` 可达**，是为 spec:144（保留输入完整性/故障恢复回归）deliberate 保留的弃用回归面。删除+移植回归会扩范围并危 :144→不删除。裁决理由见审查记录。
- **H2（merge:/style: 前缀）**：历史已合入提交不可变，不重写历史（破坏性）；本票自身提交均用约定前缀（fix/refactor/test/docs/chore）。

**延后：**

- **Spec Finding 2（Low，空正文成功版本）**：graphify 既有空文件语义、规格无明确要求、廉价不重提取→信息性，不在本次范围。
- **J2/J3/J4/J6/J7/J8/J12/J15/J17/J18**：纯重构 judgement call（code-review skill 明确永远非硬违规），undertaking 会超出"只修审查发现，不新增功能"范围并带回归风险→承认并延后（F8 为唯一琐碎安全去重例外）。

### 验证证据

- 全量 `make check`（前台等待）：doc-guard **PASS（87 documents）**、`ruff check` All checks passed、`ruff format --check` **223 files** already formatted、pytest **1138 passed**（232.17s，基线 1131 + 本票 7 条新回归），exit 0，无 flaky（interrupt 测试本轮通过）。
- 隔离安装 `make smoke`：**PASS**（wheel/sdist、CLI、mdcompare、knowledge snapshot/discover、unified 五阶段 sync/reuse、canonical state/UTC+8/锁、doctor、Web、images、托管升级/卸载），exit 0。
- 集成 tip：开工基于 `923b4e2`（含 01–08），收尾合入 `aec64b3`（仅 map.md，无冲突）。门禁日志 `/tmp/uus09-gates.log`（/tmp 易失，长期证据以本记录与实测计数为准）。

### 未完成 / 协调

- 无未完成项；审查全部发现处置完毕，9 项修复均绿。本票合入后集成分支含 01–09 全部实现。
- 真实语雀/OCR/Graphify 调用、token 消耗、生产同步未执行（规格 Out of Scope）；安装交付以隔离 `make smoke` 为授权检查。
- 收尾（用户明确授权）：push 集成分支→开 PR 到 master→评论 `@greptileai review pr` 触发审查；清理各票 worktree。

## Comments
