# 统一无人值守同步最终双轴审查记录

日期：2026-11-12。审查对象是集成分支 `unified-unattended-sync`（票 01–08 全部合入后），基线 `f09f45d`（origin/master merge-base），91 提交、60 文件、+11712/-450。按 `code-review` skill 双轴并行审查：**Standards**（是否遵循仓库编码标准 + Fowler 代码异味基线）与 **Spec**（是否忠于规格与各票公开合同）。本记录由票 09 关闭，逐项给出处置（修复 / 验证为非问题 / 延后并说明理由）与回归证据。

审查报告原文：`/tmp/uus09-review/standards.md`（20 项）、`/tmp/uus09-review/spec.md`（2 项）；orchestrator 证据裁决：`/tmp/uus09-review/adjudication.md`。`/tmp` 易失，长期证据以本记录与票 09 Answer 为准。

## 汇总

- **Standards：20 项**（2 hard：H1/H2；18 judgement：J1–J18）。
- **Spec：2 项**（1 Medium：Finding 1；1 Low：Finding 2），无范围蔓延。
- 处置：**9 项修复**（含 7 条命名回归）、**2 项 won't-fix**（H1/H2，证据裁决）、**1 项信息性延后**（Spec Finding 2）、**10 项纯重构 judgement 延后**（守票 09 不扩范围）。

## Spec 轴

### Finding 1（Medium，最重）— 已修复 F1

正文复用来源遇图片变化时，内联 OCR 成功永不进 compile。`stages/plan.py:188` 把正文相同的来源路由到 `body_reuse`（与 `compile` 互斥），`:222` 独立地把变化图片放进 OCR 名单；`stages/ocr.py` 成功后写 canonical `graph.ocr` 但**不登记 compile followup**；`knowledge_execute.py:1303/1321` 只编译 `plan.compile` 与事后 followup。于是正文复用来源的变化图片被成功 OCR 后**永不重编译**，新图片文本 silently 不进图谱。违反 spec.md:94（"成功 OCR 改变编译输入时，计划允许该来源进入 compile"）、:148（"图片变化不能被正文复用吞掉"）、:136（OCR 改变已发布编译输入应显式登记后续编译工作）。票 08 的有界离线 UAT 用纯文本文档（OCR skipped）恰好未行使该路径，故单元测试与 UAT 都漏掉。

**修复**（`cc6752a`，复用 06 既有 followup 机制，无新机制、无二次审批计划）：`stages/ocr.py` 累计 `changed_inputs`（仅 `success` 新识别，不含 `reused` 缓存命中）；OCR 结束后对属 `body_reuse` 且有 `changed_inputs` 的来源调 `register_compile_followup`（`sync_results.py:484`，按 book+doc+version 幂等）。compile 的 `_pending_followup_items`（`knowledge_execute.py:1209`）把该来源并入本批 compile 集（校验 input_available+raw+版本匹配），`_consume_followups`（:1257）仅在真实发布后标 done。reused（图片未变）与 OCR 失败不登记（无已发布输入变化，保留旧知识）。

**回归**：`test_body_reuse_changed_image_successful_ocr_is_compiled_and_publishes`（变化图片+OCR 成功→本批编译并发布新图片文本，followup 消费为 done）；对照 `test_body_reuse_reused_image_is_not_recompiled`（未变/reused 图片→不重编译，spec:148）。修复前红、修复后绿。

### Finding 2（Low）— 信息性延后

空正文来源永不推进成功版本（`update_graph.py:362/409` 把空文件排除出 `published_sources`，`knowledge_execute.py:1474-1500` 仅对 `published_ids` 推进 canonical）。为 graphify 既有空文件语义（本分支只加了审计字段），廉价（不重提取），规格无明确空文档要求。信息性，不在本次范围处理。

## Standards 轴

### 已修复

- **F2 = J1（fd 泄漏，纯缺陷）**：`instance_lock.py:86-99 lock_is_held` 打开 fd 后三条返回路径（held→True、error→False、free→False）均未 close，`dogpdkb status`（watch）反复调用会耗尽 fd。修复（`d4c6e4f`）：try/finally `os.close(fd)` 包裹 flock 探测，语义不变。回归 `test_lock_is_held_does_not_leak_descriptors`。
- **F3 = J14（body-reuse 抹掉 graph 证据）**：`stages/plan.py:294-302` 对 body-reuse 来源整体替换 `record["graph"]`，丢掉既有 `ocr`/`body_scope`；若图片全部 reusable（不在本批 OCR 名单），OCR 不会重新写回→canonical OCR 复用证据丢失。修复（`1cd94d8`）：合并保留既有 `ocr`/`body_scope` 等键，仅更新 status/coverage/version/raw/metadata。回归 `test_body_reuse_plan_preserves_prior_ocr_evidence_and_body_scope`。
- **F4 = J13（crash-resume 守卫，验证为真缺陷）**：两审查员分歧（Standards 指其永久阻断崩溃恢复，Spec 称为非问题）。implementer 实证为**真**：硬崩溃在阶段 artifact 落盘后、runner 记录完成前，留下 artifact 存在但阶段仍 `running`；resume 重跑该未完成阶段时被"output already exists"守卫永久阻断（违 spec:150）。修复（`ef56e43`）：防覆盖守卫仅限首次尝试（`context.state.stage(...).get("attempts",1) <= 1`），resume 重试经 `publish_reports` 原子重建；symlink 拒绝无条件保留，`validate_outputs` 仍拒输入冲突。回归 `test_resume_recovers_a_crash_after_the_compile_artifact_lands`、`test_resume_recovers_a_crash_between_plan_artifact_and_stage_record`（os._exit(137) 注入崩溃→resume 恢复）。
- **F5 = J16（status 错误脱敏）**：`sync_status.py:154,170` 原样打印 `{error}`。虽多为内部结构性错误（RunError/KeyError/TypeError），为一致性与防凭证形态泄漏经 `safe_diagnostic` 脱敏（`742221a`）。回归 `test_status_redacts_credential_forms_in_its_error_output`。
- **F6 = J10（票脚手架注释）**：剥离生产代码中的票/项编号注释（`sync_stages.py`、`stages/compile.py`、`knowledge_execute.py` 等），遵循"注释不引用当前任务/票/调用方"标准；保留真正 WHY 注释（`f33525f`）。无行为变化。
- **F7 = J9（死代码）**：移除核验确属未用的参数/字段（`sync_wash.py Source.save`/`base`、`stages/compile.py _anomalies` 未用 `context` 参数、`sync_run.py` 从未传入的 `resume_of` 等）（`9a4e2c2`）。
- **F8 = J5（重复字面量）**：`stages/ocr.py`、`stages/compile.py` 改为从 `stages/plan.py` 导入规范 `PLAN_SCHEMA`，不再各自重定义（`2ccead6`）。无环。
- **F9 = J11（名字遮蔽）**：`sync_status.py:120 report(view)` 参数改名，不再遮蔽模块级 `view()` 函数（`742221a`）。无行为变化。

### Won't-fix（证据裁决，不破坏）

- **H1（Standards 最重，"保留旧执行器 / test-only 接口"）**：产品入口 `workflows/sync_knowledge_base.py::main`（:447）**总是**转发统一 runner，legacy flag 仅弃用转发——spec:70 核心意图（无第二默认/产品入口分流）已满足。`knowledge_sync.__main__`（:175）与 `legacy_main`（:470）**仅** `python -m dogpdkb.knowledge_sync`/测试直接调用可达，**不从 `dogpdkb sync` 可达**。它们是为 spec:144（"其余输入完整性和故障恢复回归继续保留"）deliberate 保留的弃用回归面：旧管线的输入完整性/故障恢复回归经此继续被锻炼。删除并把全部回归移植到统一 CLI 会扩大范围（票 09 禁止）并危及受保护覆盖。裁决：**不删除旧面**，记录 :70/:144 张力与解决。
- **H2（提交前缀 merge:/style:）**：历史已合入提交不可变，**不重写历史**（破坏性）。票 09 自身提交均用约定前缀（fix/refactor/test/docs/chore）。后续合规。

### 延后（纯重构 judgement，守不扩范围）

J2（计数词汇漂移）、J3（四套原子写变体）、J4（私有跨模块导入）、J6（OCR-block 证明三处重复）、J7（数据泥团/长参数表）、J8（文档骨架构建器重复）、J12（无环 lazy import）、J15（glossary 术语）、J17（冗余 except 子类）、J18（union-yield/超长 handler）。`code-review` skill 明确这些**永远是 judgement call** 而非硬违规； undertaking 它们会超出票 09"只修审查发现，不新增功能"的范围并带来回归风险。承认并延后（F8 是唯一琐碎安全的去重例外）。

## 门禁

修复合入最新集成 tip 后跑完整 `make check` + `make smoke`（见票 09 Answer 实测计数）。判据为可观察阶段行为/产物/状态/重复调用次数/恢复结果，不以某次模型节点数或语义无损为判据。真实语雀/OCR/Graphify 调用、token 消耗、生产同步未执行（规格 Out of Scope）；安装交付以隔离 `make smoke` 为授权检查。
