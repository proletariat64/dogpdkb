# 日常知识库同步

当前合同为 CLI 编排器重写。旧 sync --knowledge、--snapshot、--dry-run、审批、
wash/anomalies 命令和旧 checkpoint 不再支持。Web 不在 sync 内。

## 分步与计划检查点

```bash
dogpdkb discover --config /srv/kb/dogpdkb.toml --book funds
# 采用上一步 stdout 给出的 differences 路径
dogpdkb apply --config /srv/kb/dogpdkb.toml --book funds --input /path/differences.json
# 采用 apply 给出的 manifest 路径
dogpdkb update-graph --config /srv/kb/dogpdkb.toml --book funds --input /path/manifest.json

# 同一实现的串行组合
dogpdkb sync --config /srv/kb/dogpdkb.toml --book funds --stop-after plan
dogpdkb sync --config /srv/kb/dogpdkb.toml --book funds --resume /path/run-directory
```

整批下载结束才比较，整批比较结束才生成计划，计划结束才 OCR，整批 OCR 结束
才发布语料与编译。停在 plan 返回0并保存输入，不触发付费处理或正式语料更新。
恢复只处理原范围；输入缺失、改变或会回退更晚成功版本时需重新生成计划。
失败或不明步骤不靠恢复自动重试。

`sync` and `apply` use download concurrency 2 by default; `--concurrency 1`, `2`
or `3` selects the per-invocation limit, capped at three. The download batch still
settles before comparison. Resume can change the value without refetching verified
completed inputs or clearing failure suppression. Interruption stops all in-flight
exports and starts no queued work. See [download concurrency](../README.md#download-concurrency)
for examples and the full scope; this is not a Skylark flag or TOML setting.

## cron 与日志

先配置工具公开入口并执行 doctor；首次运行前以 init-state 登记现有语料。首次
新增截止为本地文件最新日期；已存在来源按自身版本比较；日期精度同日仍会比较。
随后使用完整观察快照。不会回填已过滤历史缺失，不会因远端缺席删除本地知识。

```cron
15 2 * * * /home/kb/.local/bin/dogpdkb sync --config /srv/kb/dogpdkb.toml --book funds >>/var/log/dogpdkb/result.log 2>>/var/log/dogpdkb/diagnostic.log
```

采用系统 logrotate 管理日志。程序没有调度服务、内置轮转、历史引用搬迁或失败
处置平台。成功或 partial 的 sync 持锁清理本次成功临时副本与重复编译工作区，
保留失败文档 Markdown、相关图片、报告和简要运行记录。暂停、整批失败和中断不
自动清理。清理错误写 stderr，保持原同步退出码。OCR 成功缓存由 mdocr 管理。

## 清理与手动 purge

自动清理只处理本次 run，不扫描历史。partial 的失败交给后续 agent 判断，
通过新 run 处理，不要求恢复原批次；同版本失败抑制仍有效。

```bash
dogpdkb purge --config /srv/kb/dogpdkb.toml --book funds --run RUN
dogpdkb purge --config /srv/kb/dogpdkb.toml --book funds --all
dogpdkb purge --config /srv/kb/dogpdkb.toml --book funds --run RUN --force
```

run/all 必须二选一。purge 删除目标 wash 的全部内容，包含失败材料与运行记录；
暂停、失败或中断 run 需要 force。批量选择含这些 run 而未给 force 时整次拒绝，
不悄悄跳过。force 不终止进程或绕过同目标写锁，不允许越出所选 book 的 wash。
正式语料、图谱、持久 state、必要比较输入和工具缓存均不删除。
手动 purge 的删除错误返回非零；删除 run 不重置失败抑制，也不执行任何工具或回填。

## 观察与退出码

```bash
dogpdkb status --config /srv/kb/dogpdkb.toml --book funds --json
dogpdkb status --config /srv/kb/dogpdkb.toml --book funds --run RUN --watch --json
```

watch 是前台只读查询，JSON 模式每行一个快照。展示阶段、公开计数、耗时、最近
进展和产物；通过进程与写锁观察遗留 running。Ctrl-C 停止观察，不终止同步。
同目标写操作互斥，遗留 run 不构成永久锁。

| 返回码 | 含义 |
| --- | --- |
| 0 | 完成、无工作、显式计划检查点 |
| 2 | 可用集合完成且存在部分失败 |
| 1 | 完整清单、公共存储、发布等整批失败 |
| 64 | 参数错误 |
| 130 / 143 | SIGINT / SIGTERM |

mdcompare 的不确定报告会让有效完整来源自动编译，不机械造成整体 partial。
OCR partial 会保留交接并继续可用正文；Graphify 目录级整批失败保留旧图。
语料和图谱成功版本独立，后续成功不会掩盖前面失败。

[配置与使用](../README.md)、[持久状态](sync-state-design.md)、
[工具公开合同](orchestrator-tool-contracts.md)。
