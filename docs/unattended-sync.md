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

## cron 与日志

先配置工具公开入口并执行 doctor；首次运行前以 init-state 登记现有语料。首次
新增截止为本地文件最新日期；已存在来源按自身版本比较；日期精度同日仍会比较。
随后使用完整观察快照。不会回填已过滤历史缺失，不会因远端缺席删除本地知识。

```cron
15 2 * * * /home/kb/.local/bin/dogpdkb sync --config /srv/kb/dogpdkb.toml --book funds >>/var/log/dogpdkb/result.log 2>>/var/log/dogpdkb/diagnostic.log
```

采用系统 logrotate 管理日志。程序没有调度服务、内置轮转、自动 wash、历史引用
搬迁或失败处置平台。run 和证据由定时清理 agent 决定处理；持久 state 独立保留，
删除 run 不重置同版本失败抑制。OCR 成功缓存由 mdocr 管理。

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
OCR partial 会保留交接并继续可用正文；单来源图编译失败保旧，成功来源继续。
语料和图谱成功版本独立，后续成功不会掩盖前面失败。

[配置与使用](../README.md)、[持久状态](sync-state-design.md)、
[工具公开合同](orchestrator-tool-contracts.md)。
