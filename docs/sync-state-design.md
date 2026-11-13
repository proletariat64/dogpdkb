# 同步状态与恢复

新实现只保留文档版本与步骤事实、每个 run 的计划/结果和工具产物引用。
旧 `.sync-state.json`、checkpoint、run 和审批状态不导入。

## 起点与来源

`dogpdkb init-state --config FILE --book KEY` 只登记现有
`title-YYYY-MM-DD-[ID].md` 文件，不改正文或图谱，不调用提取。filename 日期只代表
日精度本地基线，不冒充精确远端版本；原 graph 成功版本未知则保留未知。
来源按 book+document ID 标识，改名、搬动维护元数据。

完整 listing 通过后才推进观察基线；缺席只记录移交。首次缺失文档按本地最新
日期截止，后续依据完整快照差异，不使用 rolling 天数或历史回填。

## 持久事实与 run

`paths.state/<book>/state.json` 保存 observation、documents 以及步骤尝试。
观察、raw 下载、语料落盘、图谱成功分别记录真实版本。工具启动前记录 running，
公开结果确认后才完成；无法确认则保持 unknown/未决以便排查。

`paths.staging/<book>/run-*/` 保存 run.json、固定计划、输入与工具报告。
成功或 partial 的 sync 自动清理本次成功临时副本，留下失败 Markdown、相关图片、
报告和简要运行记录；不自动扫描或迁移旧 run。最新比较基线及有效复用输入独立
保存在持久区，后续同步不依赖已清理的成功副本。state 必须独立保留；清理或
purge run 不会让同版本失败重新自动执行。purge 的范围仅限所选 book 的 wash。

## 失败与恢复

同版本同一步骤 failed/unknown/未决调用不自动重试。新版本可处理；没有启动的
步骤仍可在后续执行。OCR 失败不阻断有效正文编译。中断 run 不挡新 cron；正在
写入的同一目标由实际锁互斥，不通过历史 running 字段永久阻塞。

显式 resume 使用原范围与成功产物。缺失或改变的计划输入需重新生成，不偷偷
抓取远端新版本；旧计划/manifest 不允许回退已成功的新版本。图谱失败不回滚语料。
没有跨 corpus/graph 事务，也不编造成功补账。
完成并清理的 run 不再承诺恢复；partial 留下的输入用于后续 agent 排查并创建新 run。

[核心 API 合同](orchestrator-core-contracts.md)、[apply 合同](orchestrator-apply-contracts.md)、
[当前操作说明](unattended-sync.md)。
