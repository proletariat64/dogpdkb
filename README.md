# dogpdkb

dogpdkb is a knowledge-base CLI orchestrator for crontab: it selects increments,
plans whole-batch stages, calls Skylark, mdcompare, mdocr and Graphify through their
public CLIs, records actual outcomes and provides read-only status. Web publication
and software lifecycle remain separate.

## Local help

All installed CLI help is in English and works offline without configuration or
authentication. Use root help to choose a command, command help for a first
invocation, and `--help --full` for the complete local contract:

```bash
dogpdkb --help
dogpdkb sync --help
dogpdkb sync --help --full
mdcompare semantic -h --full
mdcompare knowledge -h --full
mdocr plan -h --full
mdocr run -h --full
skylark export -h --full
```

`-h` and `--help` are equivalent at each scope; `--full` requires a help flag.
Each tool supports root `--version`. Help describes its actual input schemas,
path rules, output files, side effects, exit meanings and recovery steps. Skylark
remains a separate installation; its source delivery is recorded in the CLI-help
acceptance notes, not bundled into the Python distribution.

## 日常使用

```bash
# 为可安装 CLI 创建禁用的示例，编辑 books.json 后再启用来源
dogpdkb init-config -o ./instance
# 编辑 ./instance/dogpdkb.toml，选择公开工具入口
# 登记现有语料，不改正文或图谱、不重新提取
dogpdkb init-state --config ./instance/dogpdkb.toml --book funds

dogpdkb sync --config ./instance/dogpdkb.toml --book funds
# 只扫描指定来源；仍发布到同一个知识库，可重复传 --source
dogpdkb sync --config ./instance/dogpdkb.toml --book funds --source inbox
# 全批获取输入、比较和计划；不会 OCR、写本批语料或编译
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

### Download concurrency

`sync` and `apply` accept `--concurrency N` for document exports only. The default
is **2**; allowed values are **1, 2 or 3**, with a hard maximum of three. Use one
for serial downloads. Invalid values fail before run creation or tool calls.

```bash
dogpdkb sync --config ./instance/dogpdkb.toml --book funds --concurrency 3
dogpdkb apply --config ./instance/dogpdkb.toml --book funds --input differences.json --concurrency 2
dogpdkb sync --config ./instance/dogpdkb.toml --book funds --resume RUN --concurrency 1
```

Each document uses its own Skylark export process and request/report files. The
main coordinator records settled results and checkpoints while other exports may
still run. A single export failure does not stop eligible downloads; existing
identity/version checks and same-version failure suppression remain in force.
All downloads settle before comparison starts. Listing, comparison, OCR and
Graphify concurrency are unchanged.

A resume can use a different value without invalidating completed downloads or
authorizing retries of suppressed failures. SIGINT/SIGTERM stops in-flight export
process groups, starts no queued exports and preserves truthful checkpoints.
This is a per-invocation option, not a TOML setting or a Skylark argument. Graph-only
recovery with `--retry-graph` does not restart downloads.

返回码：0 完成、无工作或停在计划；2 可用集合完成但有部分失败；1 整批失败；
64 参数错误；130/143 中断。stdout 是结果，stderr 实时输出进度与工具诊断，
可直接由 shell 重定向和系统 logrotate 管理。没有工具公开计数时不推算百分比。

同版本同一步骤的失败或不明结果不会在日常运行自动重试，由定时清理 agent 排查。
新版本仍正常处理；OCR 失败不阻断可用正文编译。缺席只记录，不自动删除既有语料
或图谱。失败图谱不回滚已更新语料；Graphify 编译按目录整批成功后发布；失败保留旧图。

成功或 partial 的 sync 自动清理本次 wash 的成功临时副本，保留失败文档 Markdown、
相关图片、报告和简要运行记录。计划暂停、整批失败和中断不自动清理；清理错误
进入 stderr，不改变同步退出码。普通 resume 不恢复已清理批次。

For a diagnosed graph failure, fix the provider/environment first, then run
`dogpdkb sync --config ./instance/dogpdkb.toml --book funds --resume RUN --retry-graph`.
This explicit recovery revalidates the retained final compile manifest, including
after cleanup, and repeats only Graphify. It preserves earlier failure reports;
target locks, source/version checks and frozen byte witnesses still apply.
Unrelated failed batches remain blocked. Historical failures may keep exit 2
even after graph recovery; inspect the current graph stage and its report.

```bash
dogpdkb purge --config ./instance/dogpdkb.toml --book funds --run RUN
dogpdkb purge --config ./instance/dogpdkb.toml --book funds --all
# 丢弃暂停、失败或中断 run；仍遵守写锁
dogpdkb purge --config ./instance/dogpdkb.toml --book funds --run RUN --force
```

purge 只删除所选 book 的 wash，包括失败材料和运行记录；正式语料、图谱、持久
版本事实、必要比较输入和工具缓存保留。它不解除失败抑制、不触发重试或历史回填。

## Authored-text routing and durable diffs

`apply`/`sync` consume the independent offline `mdcompare semantic` CLI as the
body-routing authority: **NEW / CHANGED / UNCERTAIN → compile; demonstrated
EQUIVALENT → reuse/metadata; UNAVAILABLE → defer**. Authored deletions compile.
Image changes and OCR failures/results are advisory and never promote equivalent
body text to compilation. Equivalent documents do not start fresh OCR. Usable
uncertainty compiles without a model, manual approval or hidden network call.
Missing or malformed authoritative reports stop explicitly rather than falling
back to legacy knowledge advice. Baseline-only IDs never imply remote deletion.

Complete `mdcompare-semantic-diff-1` evidence is streamed per document and indexed
outside wash under `state/<book>/semantic/<run>/`. Preserve the index and its
referenced evidence directory together. Full text, source versions, heading
context, original coordinates and add/delete/modify/move evidence are not clipped
by human-report limits. Skips and failed compilation never advance the actual
successful graph baseline; capturing raw or publishing a new document alone is
not a compiled baseline. Explicit `init-state` facts remain the bootstrap for an
existing corpus, without inventing successful graph steps.

For previously established marker repairs, optionally configure
`[paths] body_recovery = "body-recovery.json"` relative to the instance TOML.
The public `mdcompare-body-recovery-1` artifact binds identity/side to immutable
source-copy bytes and ordered UTF-8 byte-offset marker insertions. dogpdkb freezes
this artifact and witnesses once per run and validates them on resume. Changing
live configuration does not rewrite an existing plan; changed frozen evidence
requires a new plan. Invalid/unresolved body scope defers its document; recovery
never itself proves semantic equality. No automatic rescue or model call occurs.
See [tool contracts](docs/orchestrator-tool-contracts.md) and
[core routing contract](docs/orchestrator-core-contracts.md).

This routing does **not** change Graphify's text-input projection described below:
whole published Markdown, including existing inline images/OCR, remains its input.

## Optional offline semantic score routing

After `mdcompare semantic`, use `mdcompare semantic-score` with an explicit
`--request-budget` (Pi OAuth, default `openai/gpt-6.1-terra`, medium), or supply
an external `mdcompare-semantic-scores-1` manifest, then call
`mdcompare semantic-route --report semantic.json --diff-index diff.json --scores scores.json --output route.json`.
This is a separate policy stage: it never calls a model, network, Graphify or
compiler and does not alter the deterministic `semantic-1.0` report. Scores bind exact sidecar bytes using SHA-256 and must match the index
report digest; invalid/duplicate/stale bindings fail without publishing. Default
threshold 3 means strictly greater scores compile, lower/equal scores skip;
`--skip-ambiguity-intercept yes` only converts ambiguous lower/equal skips to
compile. Missing scores never enable skips. See
`mdcompare semantic-route --help --full` for the complete schema and path rules.

Enable bounded scoring in `dogpdkb apply/sync` with `[semantic_score]` and
`request_budget = N` (default `0`, offline). Optional `model = "provider/model"`
overrides the scorer default. Plans freeze the exact score and route artifacts;
a resume does not call the scorer again. Ambiguity is advisory.

## 多源订阅

`books.json` 中每个订阅有一个 corpus 和 graph，`sources` 显式列出输入：

```json
[
  {
    "key": "funds",
    "sources": [
      {"key": "team", "type": "yuque", "url": "https://example.invalid/team/book", "document_ids": ["123", "456"]},
      {"key": "inbox", "type": "directory", "path": "./inbox"}
    ]
  }
]
```

`directory` 路径相对 `books.json`，递归接收 `.md`（不区分大小写），跳过隐藏项、
`_inventory` 和符号链接；不移动或删除原件。非 Markdown 文件不作为独立文档输入；
Markdown 引用的目录内资源随正文冻结，资源变更也计入版本。
默认扫描所有启用来源；可重复传 `--source KEY` 选子集，未选来源不访问也不判为缺失。
任何选中来源扫描失败则不生成计划。`--resume` 不接受 `--source`，只使用冻结输入。

语雀来源可省略 `document_ids` 订阅整本；列表仍完整读取并验证，再按 ID 筛选。
来源 key 在订阅内唯一，已记录状态后不允许换绑 URL、订阅范围或目录。
旧的顶层 `url` / `document_ids` 配置不再接受；使用新版前请先升级实例配置。

## 配置与工具边界

显式 `--config` 优先于 `DOGPDKB_CONFIG`；源码 checkout 默认读取 `default.toml`，
安装包默认读取当前目录 `dogpdkb.toml`。TOML 路径相对配置文件，订阅 `corpus`
相对 books.json。多个启用的订阅必须指定 `--book`。凭证只放工具既有环境。

```toml
[tools]
mdcompare = "mdcompare"
mdocr = "mdocr"
graphify = "/opt/graphify/bin/graphify"
skylark = ["node", "/opt/pi-skylark/dist/cli.mjs"]
```

编排器对工具只调用公开 CLI、读取公开产物，不导入工具包或复刻其内部算法。
本轮新增能力必须先提供给所选工具入口：

- [mdcompare catalog、mdocr plan/run](docs/orchestrator-tool-contracts.md)，随本仓库 wheel 提供。
- [Graphify 原版 CLI](docs/orchestrator-graphify-contract.md)：目录级增量、原生 manifest/cache 和整批发布。
- [Skylark listing/export](docs/orchestrator-skylark-contract.md)：已恢复的独立 CLI 与构建方法。

`sync` and `update-graph` default to `--graphify-inputs text`: only Markdown
(`.md`, case-insensitive) is passed to Graphify. OCR text remains in Markdown;
images and other resources remain in the corpus without being sent for extraction.
Use `--graphify-inputs all` for the previous complete-tree scan. Switching to text
can natively remove non-Markdown sources from the graph; source files are retained.

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
