# 08: 验收：回归收敛、离线 UAT、门禁与安装 smoke

Type: task
Status: ready-for-agent
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

## Comments
