# Sync wash 实施验收

Status: complete

## 交付

集成分支 `feat/sync-wash-cleanup`，基线 master `9615a85`，最终运行代码 `193992e`。
实施规格见 [Sync wash 清理规格](spec.md)，审查见 [代码评审](review.md)。

- completed/partial sync 只清理本次成功临时副本；失败 Markdown、相关图片、报告保留。
- 最新比较/OCR 输入和见证独立于 wash；清理不改变失败抑制或触发重试。
- purge 支持 run/all、工作目录相对路径和 force；遵守 book 范围、写锁与保护数据边界。
- 清理错误不改变原同步退出码；资源清单不确定时保留输入并报告清理失败。
- 无外部工具源码改动。`src/dogpdkb` Python 物理行数 3456 → 4039（+583）。

## 验证

在干净验证工作树针对 `193992e` 执行：

- `make check`：doc-guard、Ruff check/format 通过；874 tests passed，111.52s。
- `make smoke`：离线构建 wheel/sdist、隔离安装通过；plan/resume、自动清理、
  no-op、purge/force、持久文件字节保留通过。使用真实 mdcompare 和无图片 mdocr CLI；
  Skylark/Graphify 使用外部命令替身。
- 三个 wash CLI 文件 46 tests passed；修复相关组合 105 tests passed。
- 实际 mdocr partial/mixed-image CLI 使用本地 PNG 且移除令牌，验证图片仍可准备/读取；
  没有调用付费或生产 OCR，也没有运行真实 Graphify 提取。
- 双轴首轮 Standards 0、Spec 2；修复后定点复核无剩余问题（Spec 13 tests passed）。
- OCR 首轮 11 文件、4 条意见已逐项复现处置；第二轮两组请求超时，
  记录于 [原始报告](ocr-review.txt)，不声称完整双轮审查通过。
- 代码图在验证工作树按实际默认分支 origin/master 更新/分析，HEAD 匹配；
  部分审查子进程使用 CLI fallback，限制见评审记录。

原始日志保留在 `/tmp/dogpdkb-wash-final-fixed-check.log` 和
`/tmp/dogpdkb-wash-final-fixed-smoke.log`。后续仅验收文档归档，文档门禁另行检查。

## 数据与验证限制

原工作区的既有未跟踪 UAT/备份被 doc-guard 当作工程文档扫描，基线有 2511 个
格式问题。未修改或隐藏这些数据；全量门禁在干净工作树执行。
生产语料、图谱、真实 wash、凭证和用户未提交数据均未删除。此验收证明隔离
CLI 合同及安装包行为，不等同于生产清理或远程 OCR/Graphify UAT。

四个 implementer 工作树及其临时分支已确认合入后删除；验证工作树在最终文档
检查后移除。集成分支保留供审查，未合并 master、推送、发布或执行真实 purge。
