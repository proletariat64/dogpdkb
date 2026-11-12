# 串行 apply、计划、语料与唯一 sync

Status: resolved
Type: task
Blocked by: 01, 02, 04
Spec: ../spec.md

## Scope

实现整批下载→mdcompare CLI→plan→mdocr CLI→语料实际落盘/manifest，sync 与分步共用函数。支持停plan和显式恢复；固定输入；失败版本/不明结果移交；不抑制合法后续正文编译。输入缺失变动和版本回退明确拒绝。

## Acceptance

安装CLI黑盒12组相关场景；工具替身+真实Markdown；验证顺序、partial、跨批失败不重试、新版本、same raw复用、plan停点、corpus与graph版本分离。

## Workflow

独立 worktree/branch，基于 rewrite/cli-orchestrator 当前 tip；先读 tdd 技能，以已确认 CLI seam 做 red→green。完成前合入 integration tip，运行相关检查，提交后报告 SHA。所有 shell rtk 前缀，所有代码关系先 code-review-graph repo_root=本工作树。禁止生产/付费调用，禁止修改用户原 checkout。

## Comments


- 2026-11-12：实现 `apply_run` 与唯一 `sync_run`；公共 API/manifest、恢复与 pending 交接见 `docs/orchestrator-apply-contracts.md`。apply/planning/sync 共 943 行。
- 公开 CLI TDD red→green：旧 apply 不识别 plan 停点；恢复初始缺少 OCR/corpus 阶段；图片变化但正文复用误判 reuse；opaque v2→v10 被词法比较拒绝；mdocr staging 相对链接搬入 corpus 后失效。对应路径均已修复并加回归。
- 13 个 apply/sync subprocess 场景通过：真实本地 Markdown、真实 mdcompare/mdocr（无付费 OCR）与 Skylark/特殊故障 CLI 替身；覆盖 plan 停点、恢复/输入变化/回退、OCR partial 与版本抑制、新版重试、整批阶段顺序、源级中断后未启动工作、plan 前固定范围恢复、opaque 版本及比较报告合同失败/合法 UNKNOWN。
- 合并 04 integration 后 32 项相关 CLI 检查通过（13 apply + 10 discover + 4 core + 5 mdocr batch）；ruff、diff-check 通过，图索引更新并对 `origin/master` 分析。只调用公开 CLI，不导入工具运行时；真实 OCR/Graphify、安装验证与生产验收未执行。
- 每阶段内部按来源串行 CLI，只在真正调用当前来源前记 attempt。共享 OCR 停止后的未启动来源保留 `pending_steps:["ocr"]`，图谱阶段不得清掉其 pending。Skylark 请求期限和 mdocr 在途期限由工具负责，无任意整批 +5 秒终止。
- 06 提供 `dogpdkb.graph.run_graph` 后验证完整 sync 末段；本轮 sync 已验证与 discover/apply 共用运行记录并停在 plan，未假称 Graphify 集成已通过。

## Answer

唯一串行 apply/sync 已完成：整批下载、比较、固定计划、OCR、正式语料与 manifest。CLI 测试覆盖暂停/恢复、部分失败、版本抑制和未启动工作；最终审查补充了 OCR 期间输入变化拒绝。合同见 [apply](../../../docs/orchestrator-apply-contracts.md)，结果见 [acceptance](../acceptance.md)。
