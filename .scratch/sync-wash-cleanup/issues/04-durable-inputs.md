# 将后续同步所需输入独立于 wash 保存

Type: implementation
Status: resolved
Assignee: wash_inputs
Parent: ../spec.md
Blocked by:

## Scope

apply.py / records.py / graph.py。保留每文档最新原始 Markdown、资源、必要 OCR 复用结果与发布见证到 state 管辖的持久输入区，不扫描迁移历史 run。通过公开 CLI 验证删除旧 run 后真实增量比较与 OCR 复用正确，失败抑制保持。避免保存成功 run 全套历史副本。

规格：../spec.md；事实：../facts.md。

测试边界沿用规格已约定的公开 CLI、退出码、磁盘产物和 status，不测试工具内部。

## Answer

Implemented per-document latest durable input storage at state/<book>/inputs/<document-id>:
raw source/resources, OCR request/report/output and declared run-owned image artifacts,
corpus/graph byte witnesses and latest graph success report. Called after plan validation
and before corpus publication; no historical run migration. The raw baseline remains
separate from enriched corpus, and OCR unknown reports remain best-effort partial.

Validation: six new public-CLI regression scenarios plus existing apply/graph suites:
60 passed; focused Ruff passed. Tests use real local Markdown/files and fake tool CLIs,
no live credentials, network OCR, extraction or production deletion.

Known integration follow-up: absolute raw Markdown image references into a run remain
spelled as original paths even though the public resource catalog maps retained bytes.
Relative image references including ../ are preserved. Root must close absolute-image
reuse behavior before final acceptance; this is a named remaining risk, not UAT proof.

### Absolute-image follow-up resolved

Added red→green public CLI regression: first partial auth stop leaves a second
image-bearing document unattempted; delete the entire old wash; new apply can read
its absolute local image through the retained catalog bytes. Comparison reports
UNCHANGED and keeps the original resource_id. Canonical raw Markdown stays byte-identical;
only a tool-facing OCR copy relocates exact catalog-declared targets, with resource
IDs mapped back in consumed reports. Seven new CLI scenarios plus existing apply/graph
suites: 61 passed; focused Ruff passed. Previous named absolute-input risk is closed.

### Formal partial-source relative-image check

Public catalog of the published corpus, with no resource overrides and after deleting
the fixture's wash, reproduced an unreadable relative image. Fixed only inputs.py:
shared explicit catalog-destination relocation applies to returned OCR Markdown and
raw fallback publication. Canonical raw bytes and resource IDs remain unchanged.
Parametrized CLI regression covers partial output present and absent, with real PNG
files and no network. All durable-input/apply/graph suites: 63 passed; Ruff passed.
