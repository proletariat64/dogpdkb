# dogpdkb

dogpdkb 是供 crontab 调用的知识库 CLI 编排器：选择增量、生成计划、串行调用
Skylark、mdcompare、mdocr 和 Graphify，保存真实结果，并提供只读状态观察。
Web 发布与软件生命周期独立。

## 日常使用

```bash
# 为可安装 CLI 创建禁用的示例，编辑 books.json 后再启用来源
dogpdkb init-config -o ./instance
# 编辑 ./instance/dogpdkb.toml，选择公开工具入口
# 登记现有语料，不改正文或图谱、不重新提取
dogpdkb init-state --config ./instance/dogpdkb.toml --book funds

dogpdkb sync --config ./instance/dogpdkb.toml --book funds
# 全批下载、比较和计划；不会 OCR、写本批语料或编译
dogpdkb sync --config ./instance/dogpdkb.toml --book funds --stop-after plan
# RUN 为输出中的 run ID 或目录；只推进未完成步骤
dogpdkb sync --config ./instance/dogpdkb.toml --book funds --resume RUN

dogpdkb status --config ./instance/dogpdkb.toml --book funds --json
dogpdkb status --config ./instance/dogpdkb.toml --book funds --run RUN --watch
# 机器观察：每行一个只读 JSON 快照
dogpdkb status --config ./instance/dogpdkb.toml --book funds --watch --json
```

`discover` 只扫描；`apply --input DIFFERENCES` 下载、比较、计划、OCR 并更新正式
Markdown 语料；`update-graph --input MANIFEST` 编译。`sync` 共用这套实现，顺序为
全批扫描 → 全批下载 → 全批比较 → 计划 → 全批 OCR → 语料落盘 → 图谱更新。
详见[操作合同](docs/unattended-sync.md)与[状态说明](docs/sync-state-design.md)。

返回码：0 完成、无工作或停在计划；2 可用集合完成但有部分失败；1 整批失败；
64 参数错误；130/143 中断。stdout 是结果，stderr 实时输出进度与工具诊断，
可直接由 shell 重定向和系统 logrotate 管理。没有工具公开计数时不推算百分比。

同版本同一步骤的失败或不明结果不会在日常运行自动重试，由定时清理 agent 排查。
新版本仍正常处理；OCR 失败不阻断可用正文编译。缺席只记录，不自动删除既有语料
或图谱。失败图谱不回滚已更新语料；Graphify 编译按目录整批成功后发布；失败保留旧图。

成功或 partial 的 sync 自动清理本次 wash 的成功临时副本，保留失败文档 Markdown、
相关图片、报告和简要运行记录。计划暂停、整批失败和中断不自动清理；清理错误
进入 stderr，不改变同步退出码。partial 后续由 agent 创建新 run 处理，不恢复旧批次。

```bash
dogpdkb purge --config ./instance/dogpdkb.toml --book funds --run RUN
dogpdkb purge --config ./instance/dogpdkb.toml --book funds --all
# 丢弃暂停、失败或中断 run；仍遵守写锁
dogpdkb purge --config ./instance/dogpdkb.toml --book funds --run RUN --force
```

purge 只删除所选 book 的 wash，包括失败材料和运行记录；正式语料、图谱、持久
版本事实、必要比较输入和工具缓存保留。它不解除失败抑制、不触发重试或历史回填。

## 小订阅

在独立 `books.json` 来源中设置 `document_ids`，并使用独立 key 和目标目录：

```json
[{"key":"small","url":"https://yuque.antfin.com/group/book","document_ids":["123","456"]}]
```

省略该字段订阅整本；显式列表必须非空且 ID 唯一。仍完整读取列表以验证来源，
随后仅选择指定文档。已建立状态后变更 ID 范围需要新的 key/state，避免旧计划
跨订阅恢复。小订阅同样使用普通 `sync`、`--stop-after plan` 和 `--resume`。

## 配置与工具边界

显式 `--config` 优先于 `DOGPDKB_CONFIG`；源码 checkout 默认读取 `default.toml`，
安装包默认读取当前目录 `dogpdkb.toml`。TOML 路径相对配置文件，来源 `corpus`
相对 books.json。多个 enabled 来源必须指定 `--book`。凭证只放工具既有环境。

```toml
[tools]
mdcompare = "mdcompare"
mdocr = "mdocr"
graphify = "/opt/graphify/bin/graphify"
skylark = ["node", "/opt/pi-skylark/dist/cli.mjs"]
```

编排器对工具只调用公开 CLI、读取公开产物，不导入工具包或复刻其内部算法。
本轮新增能力必须先提供给所选工具入口：

- [mdcompare catalog、mdocr batch](docs/orchestrator-tool-contracts.md)，随本仓库 wheel 提供。
- [Graphify 原版 CLI](docs/orchestrator-graphify-contract.md)：目录级增量、原生 manifest/cache 和整批发布。
- [Skylark listing/export](docs/orchestrator-skylark-contract.md)：已恢复的独立 CLI 与构建方法。

Graphify 直接使用未修改的已发布 CLI；已有图缺少其原生 manifest 时停止，
不自动回填历史。Skylark 使用已恢复的独立 CLI，配置它的真实可执行路径。

## 安装与独立功能

见[安装](docs/installing.md)、[交付边界](docs/platform.md)。现有 managed 安装的
`dogpdkb update --version VERSION`、`dogpdkb uninstall` 保留，配置和用户数据保留。
`dogpdkb publish-web` 是独立操作，sync 不调用它。Web 产物和生命周期行为不纳入
同步重写；发布软件、部署站点或真实付费处理需另外授权。

仓库包含 `src/dogpdkb/` 编排器、独立 `src/mdocr/` 和 `src/mdcompare/` 工具、
`web/kb/`、工程脚本与测试。机器上的语料、图谱、缓存和运行产物不提交 Git。
旧参数、旧状态、旧 run 不迁移；旧流水线和内置 wash/anomalies 平台已移除。

## 开发验证

优先复用已可用的 Python 3.12 环境。新环境使用 `make bootstrap`；不要在已有
共享环境执行会删除其他包的精确同步。

```bash
make check   # 文档、Ruff、测试
make build   # wheel + sdist
make smoke   # 隔离安装后通过公开 CLI 验证
```

CLI 合同测试使用真实本地 Markdown 与外部工具替身；独立 mdcompare/mdocr 与
上游 Graphify/Skylark CLI 测试另列。替身测试、隔离安装、付费 OCR/提取和生产 UAT
是不同证据，本次不自动执行付费或生产任务。

[mdcompare](docs/mdcompare.md)、[OCR 合同](docs/spec/mdocr.md)、
[文档规则](docs/doc-guard.md)、[发行流程](docs/releasing.md)。旧 UAT 和评审文档
保留为历史证据；当前重写合同见 `.scratch/cli-orchestrator-rewrite/spec.md`。
