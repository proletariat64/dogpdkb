# 02: 正式化完整观察快照与增量导出

Type: task
Status: resolved
Blocked by: none

## Context

- [规格](../spec.md) 是完整契约，本票范围不删减规格安全边界。
- 集成分支：daily-knowledge-sync。测试入口已由用户确认：现有CLI黑盒；外部服务使用替身，无生产/付费操作。
- 当前v3及研究实物作为起点，保留所有旧工作。

## Acceptance and ownership

复用现有语雀代理/分页和导出能力，新增可安装的knowledge_capture模块，从现有dogpdkb discover入口用--snapshot显式启用新路径，旧discover行为不变。新增--baseline-snapshot、--staging；为CLI黑盒可重现支持--listing本地完整列表输入及--details-dir本地详情输入（与真实适配器等价，禁止默默联网补缺）；远端模式复用已配置book/source。不要改sync主入口，留给03。

定义并在Answer记下公开snapshot JSON契约、实际CLI用法，供03消费。包含book身份、完整列表唯一ID、页覆盖/完整性、观察起止时区、准确UTC时间与实际字段、new/updated/renamed/moved及缺失/异常、详情与列表时间、所选raw和原文阶段；标签重叠只导出一次。初次无baseline只初始化完整观察，不全库下载/判新增；不完整/无时间/倒退不得假成功。现有成功raw可引用、基线禁止覆盖。支持有界重试、私有检查点与续跑；pending已知身份可通过--pending-state或等价显式输入再次导出，远端没再变化仍保留。捕获不改语料/图谱成功版本，不执行OCR/Graphify。CLI替身测试从现有入口验证，不仅单测内部。主要拥有新knowledge_capture模块、get_sync_updates.py最小分发改动、tests/test_knowledge_capture.py；避免改cli.py和sync_knowledge_base.py。

## Working rules

在独立worktree基于集成分支工作；读取tdd skill及相关测试指引，一条失败的CLI行为测试开始再实现；每轮记录red/green证据。完成后合入最新集成分支再测试，提交本票实现并将Status改resolved，Answer列实际验证和公开合同。任何测试失败/部分实现保持claimed，不假结案。不得运行reset/clean或删私有实物。

## Answer

### 公开 CLI

远端模式复用已配置的 book/source、现有代理、有界重试、分页、TOC 与 `export_doc`：

```bash
dogpdkb discover -s <book-url> --snapshot <snapshot.json> --staging <private-dir>
dogpdkb discover -s <book-url> --snapshot <next.json> --baseline-snapshot <snapshot.json> --staging <private-dir> [--pending-state <prior-snapshot.json>]
```

离线替身模式使用相同入口，不会静默联网补缺：

```bash
dogpdkb discover -s <book-url> --snapshot <snapshot.json> --staging <private-dir> --listing <listing.json> --details-dir <details-dir> [--baseline-snapshot <snapshot.json>] [--pending-state <prior-snapshot.json>]
```

`--listing` 输入是 `{book, listing, documents}`。`book` 含整数 `book_id/items_count` 及 slug/name；`listing` 含 `complete: true`、`document_count`、连续 `pages[{offset,limit,item_count}]`；`documents` 是唯一整数 ID 的完整列表，每条可含 `toc_path`。`--details-dir/<doc_id>.json` 与详情适配器等价，必须有字符串 `body` 和可用版本时间；本地列表选中但详情缺失会失败，不调用远端。`--staging` 缺省为 snapshot 同级的 `staging`。

### `knowledge-capture-1.0` snapshot 契约

- `schema_version` 固定为 `knowledge-capture-1.0`；`status` 为 `initialized`（无 baseline 的完整初次观察）、`captured`（完整增量观察）或 `incomplete`（存在导出/版本异常）。initialized/captured 退出 0，incomplete 和输入错误退出 1。
- `book` 是书籍身份；`observation` 含实际 `started_at/ended_at/timezone=UTC`；`baseline` 含基线 observation 和文档数。所有版本比较按 UTC 瞬时，不用字符串或日期比较。
- `listing` 含 `complete/document_count/document_ids/pages`。唯一 ID、数量守恒、连续页和终止页均校验；不完整列表不能形成成功结论。
- `documents` 以字符串 ID 为键，每项含整数 `id/title/slug/format/toc_path/version`。`version={field,value,utc}` 记录实际使用 `content_updated_at` 或 `updated_at`、原值和 UTC 值。版本一致且已有 raw 时含 `raw`。
- `changes` 是 `new/updated/renamed/moved/absent` 五个整数 ID 数组；标签可重叠。`absent` 不表示删除。`selected` 是当前仍存在的待导出 ID 去重排序数组。`counts` 分开记录文档、各标签、唯一 selected、captured/failed、pending 和 anomalies。
- `exports` 以字符串 ID 为键，仅含 selected。成功项含 `status=captured`、`reused`、`labels`、`raw`、`listing_version`、`detail_version`、`original`；失败项含 `status=failed`、标签、列表版本、original 和有界错误原因。列表与详情时间分别保存；详情早于或晚于列表均产生 anomaly 和 pending。
- `raw={path,stage=raw_export,captured_at,adapter}`。`path` 相对本 snapshot 所在目录解析；adapter 为 `local_detail` 或 `skylark_doc_detail`。这是现有 Markdown 导出适配器输出，不是 OCR 或历史富化正文。`original.status` 为 `not_applicable`（新 ID）、`available`（含重定位后的基线 raw）或 `missing`（含 `baseline_raw_missing`）。unchanged 观察会继承仍存在的 raw 引用，供后续更新作 original。
- `pending` 是 `[{id,reasons}]`；`anomalies` 是 `[{id,code}]`。`--pending-state` 接受同 schema、同 book 的 snapshot 并读取其 pending；已知 ID 即使远端时间不变也再次选择，当前缺席 ID 仍保留 pending，未知 ID 拒绝。
- 私有检查点 `<staging>/capture-checkpoint.json` 使用 `knowledge-capture-checkpoint-1.0`，按 book 和列表版本复用已存在的成功 raw。staging 0700，raw/checkpoint/snapshot 0600，JSON 原子写入。完整 snapshot 与 baseline 禁止覆盖；只有 incomplete snapshot 可在原输出路径续跑。

03 可直接消费 `documents`、`changes`、`selected`、`exports`、`pending` 和版本/raw 引用。捕获不写语料或图谱成功状态，不执行 OCR、Graphify、发布或基线推进。

### 验证

TDD 从 CLI 拒绝 `--snapshot` 的失败测试开始；随后分别复现并修复增量分类/重叠导出、时间倒退、详情晚于列表、pending 保留、失败续跑、original 路径、分页缺口、完整快照覆盖、远端适配器、raw 跨观察继承和 baseline 身份一致性缺口。

合入集成 tip `c50c77900c59610db69fa321c18c46bc70075fd6` 后：

```text
195 passed: test_knowledge_capture.py + test_sync.py + test_platform.py + test_mdcompare_knowledge_cli.py + test_mdcompare_knowledge_units.py
Ruff check: passed
Ruff format --check: passed
LSP changed paths: 0 errors
```

远端测试使用本地假 MCP 代理；所有自动化均未访问真实语雀，未运行 OCR/Graphify，未修改生产语料、图谱或基线。
