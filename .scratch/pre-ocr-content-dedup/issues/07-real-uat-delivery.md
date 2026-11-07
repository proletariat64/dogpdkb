# 07: 真实文件 UAT 与最终交付验收

Type: task
Blocked by: 02, 04, 05, 06
Status: claimed

**What to build:** 用 co.bot 已固定的 98 对真实材料运行 mdcompare，并完成全部契约的集成验收、使用文档、代码审查和交付记录。UAT 必须使用真实文件，不用合成文件冒充现场证据。

- [x] 先阅读已核验资料包指南，按最终 schema 创建独立适配清单，明确旧文件为历史 OCR 富化文本、新文件为 raw；不把路径索引直接当正式输入。
- [x] 使用固定 98 对而非持续变化的生产目录，按稳定 ID 配对；可远端只读运行或同步受限副本至当前工作区，资料和正文报告保持私有且不提交 Git。
- [x] 对 98 个 incoming 分别生成恰好一个结果，核对统计守恒、原因码、差异证据上限和重复运行确定性；不以 27／45／26 人工分类作为 CLI 动作金标准，不设过滤率目标。
- [x] 确认缺旧 raw 或资源证据时保持 UNKNOWN，不能确认图片未变或 OCR 可复用；重点核查指南所列金额、字段、规则及不明转写案例。
- [x] 记录真实资料实际覆盖与缺口。真实材料不足以证明正常旧 raw 对新 raw 或完整图片复用时如实报告，不伪造已成功发布基线；合成反例属于开发测试，不冒充真实 UAT。
- [x] 不得远端抓取、运行 OCR、Graphify、生产同步、推进基线或修改现场输入与同步状态；仅生成调用者指定的报告。
- [x] 使用文档说明安装调用、清单／报告 schema、规范化、比较预算、原因码、动作优先级、退出码、隐私边界及历史输入限制，明确比较结果不等于业务正确或生产上线证明。
- [ ] 核对规范全部必测项和六种动作，运行适用类型检查及仓库要求的检查，最终运行完整测试套件，完成代码审查并修复发现的问题。
- [ ] 交付记录列出真实 UAT 命令、输入来源、结果计数、退出码、限制与审查证据；按本地 tracker 规范附验证依据并解决所有 tickets，不把资料整理当作 UAT 执行成功。

## Answer

Implemented the delivery guide, sanitized real-file UAT record, and the isolated
wheel/sdist installation gate for mdcompare. This ticket remains claimed until
the integration owner finishes independent standards/specification review and
records any fixes. No implementation or UAT proof is being substituted for that
remaining review gate.

- Base: integration 88bd9ba, including the shared complete prepared-JPEG fix.
- Real UAT: two complete fixed-98 historical_enriched-to-raw runs, exit codes 2/2,
  98 unique ordered results and 98 UNKNOWN each. JSON and human reports are
  byte-identical. All 307 source files / 28,077,256 bytes stayed directly
  byte-identical before/after. All requested reports use mode 0600.
- Default explanation limits: 87 documents with bounded evidence, 11 with
  DIFF_BUDGET_EXCEEDED at 2,000,000 work steps. All five guide IDs had observed
  differences. A separate real amount case with a bounded 128,000-character
  display budget retained the old 100k/new 300k values without clipping and
  stayed UNKNOWN; the APD/AP2 case was visible at the default display budget.
- Input stage and resource gaps are explicit: no successful old raw or complete
  paired images exist. 89 resource/OCR dimensions UNKNOWN and 9 NOT_APPLICABLE;
  no verified OCR reuse or filtering-rate claim. Metadata adaptation is not
  production metadata gold. No production inputs, checkpoints or baselines changed.
- Private retained manifests, reports, verification and both unpublished build
  artifacts: integration worktree tmp/mdcompare-uat-20261107/results/. Full source
  evidence is next to it in source/. Neither is submitted to Git or review tools.
- TDD installation seam: new CLI regression first failed because a distribution
  omitting mdcompare was not diagnosed before attempted installation; explicit
  wheel/sdist checks make it pass. Existing positive-report tests now explicitly
  require a report before indexing it, preserving the global-failure None cases.
- Full repository suite, once after the integration JPEG correction and delivery
  code changes: PYTHONPATH=/tmp/mdcompare-ticket07/src <integration>/.venv/bin/python
  -m pytest -q --durations=20: 693 passed in 27.03s.
- Ruff check and format across the repository passed; doc-guard passed 38
  engineering documents. Cached Pyright with explicit typeshed and the reused
  interpreter checked all mdcompare source plus its seven test files: 0 errors,
  0 warnings. The only environment advisory is the absent optional .venv inside
  this isolated worktree; the explicit existing interpreter was used.
- Offline uv build produced the 0.1.6 wheel and sdist from the above core plus
  this ticket's installation checks/docs. The existing smoke command passed with
  cached offline dependencies and disposable environments. It verified installed
  mdcompare import provenance, version/help and a real CLI invocation on its
  synthetic installation fixture, then the existing platform managed
  upgrade/uninstall checks. That synthetic fixture is installation proof only,
  separate from real UAT; no live authenticated download, remote CI, production
  integration, OCR execution or release publication was verified or performed.
- Usage and output contracts: docs/mdcompare.md; real-file evidence summary:
  docs/mdcompare-uat.md. README links both. The parent spec is unchanged.

Pending: integration-wide code review, fixes if needed, then root resolves this
ticket and updates the implementation map. The final two acceptance boxes remain
open for that integration closure.


### Final review corrections (pending integrated revalidation)

- Spec review's high finding was reproduced through the CLI: image inputs and CSS imports with no resource evidence incorrectly returned SKIP_VERIFIED. Unsupported resource-bearing HTML/CSS attributes and active content now retain UNKNOWN; ordinary navigation/static tables and proven standalone code examples remain resource-free.
- CRLF and lone-CR reference destinations are recognized in an internal CommonMark parsing view, without changing the text comparison. VT, NEL and Unicode line separators cannot manufacture fenced-code boundaries. Backticks in uncertain raw-HTML-block context do not hide active images. Each unsafe case was reproduced red before correction.
- Malformed bracket, angle and backtick inputs exceeded a 3-second CLI timeout before correction. A cumulative per-document syntax estimate now runs before masking or regex matching and returns RESOURCE_BUDGET_EXCEEDED above 20,000,000; a multiline regression proves the cap accumulates across lines.
- OCR's medium stable-schema finding was reproduced for unread baseline and incoming files. Complete UNKNOWN dimension shapes are preserved, with null unverified equality/reference facts and no invented false equality; known later-stage partial evidence is retained.
- Confirmed resource removals are recorded before obsolete byte reads, so a missing removed resource does not erase the known removal or create processing work. Explicit duplicate-JSON-field, file-budget and read-time drift reason codes remain global exit-1 diagnostics instead of being flattened; CLI regressions prove no body/path leakage.
- OCR record maps and original-resource ID indexes are created once per document, lazily when unchanged images need reuse verification. Changed images bypass malformed/oversized obsolete records; mixed per-image reuse and uncertainty remain visible. No persistent index or hash was added.
- Removed the unused atomic_write wrapper, unused compare_ocr options argument and unreachable raw-history guard; internal documentation follows the live interfaces. The one historical default merge subject is retained to preserve commit/evidence pointers; subsequent task merges and corrective commits use conventional prefixes.
- Twenty-nine additional CLI regression cases cover these corrections. The focused comparator/distribution suite passes 194 tests. Ruff/format, doc-guard, and Pyright over the comparator plus all seven comparator test files pass; Pyright reports zero errors/warnings.
- The successful external OCR review selected/completed 15 files, failed none and reported seven comments. Its reported code issues are addressed in this correction; root will independently recheck and aggregate review dispositions.
- Final full-suite, real-file UAT repeats, rebuilt installation smoke and closure remain the integration owner's gates. Earlier reports/artifacts must be preserved before final replacements. Status remains claimed.
