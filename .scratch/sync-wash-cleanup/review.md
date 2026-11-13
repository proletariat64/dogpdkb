# Sync wash 代码评审

Status: resolved

基线 `origin/master`（9615a85），首轮审查集成提交 `8e926fc`。

## Standards

Reviewed `origin/master...8e926fc` in `/tmp/dogpdkb-wash-validation`.

#### Documented standards

No confirmed violations found. External tools remain subprocess/artifact-only; no Graphify/Yuque source changes, private imports, new fingerprints, historical migration or automatic retry layers were introduced. Wash deletion is confined to explicit run ownership, write locks and protected-data boundaries.

#### Smell baseline

No actionable baseline smells found. The added persistence, cleanup and purge modules have separate lifecycle responsibilities; their small path checks and artifact transformations do not justify a generic cleanup/reference framework.

#### Limits

Graph MCP calls were attempted but became unavailable during review. CLI fallback could not read the parent-built schema-v15 graph because installed CLI supports v13. No graph state was altered. Used the parent-verified graph coverage/HEAD and targeted diff inspection; no global relationship claims were inferred from an incomplete graph. A temporary public-CLI probe with a local image fragment (`diagram.png#fig`) passed after fixture wash deletion. No production inputs or directories were used.

## Spec

Reviewed `git diff origin/master...8e926fc` in `/tmp/dogpdkb-wash-validation`; base `9615a85`. Graph-first MCP was attempted but became unavailable; CLI fallback could not read schema v15 with installed v13. Targeted source and public CLI reproductions provide the evidence below.

#### Findings

1. **High — failed export can lose its related pictures** — `src/dogpdkb/cleanup.py:42-47`. Spec: “保留失败文档已有的 Markdown、相关本地图片、报告”. A rejected export is absent from the plan; cleanup scans only `source.parent` (`raw/`). A retained Markdown using `../images/2.png` therefore loses the picture under sibling `run/images/`. Reproduced through `sync` with the existing `tests.test_wash_cleanup_cli.instance` fixture: add that local PNG and Markdown reference for source 2, return export status failed. `/tmp/wash-spec-export-review-4o6hd7ke/instance.toml` records exit 2 and cleanup completed, raw Markdown exists, image was deleted. Retain the failed source’s catalog-declared local resources across the current wash rather than assuming they are descendants of the raw file’s parent. Do not retain the whole run or parse tool internals.

2. **Medium — Graphify failure handoff loses the executed command** — `src/dogpdkb/graph.py:154-165,250-260`. Spec: “步骤、命令和退出码/原因”. Graphify bypasses `apply.call_tool`; failure rows and result JSON contain no argv. `clean_run` also deletes `graphify.log`, and the retained run’s last tool metadata still describes mdocr. Thus a subsequent agent cannot recover the executed Graphify entrypoint/options. Record its actual sanitized argv in the Graphify failure/result before cleanup. Reproduced at `/tmp/wash-spec-argv-review-vt2n3gar/instance.toml` using the existing cleanup fixture with Graphify exiting 1: `sync` exits 2; graph failures have no argv and graphify.log is absent.

No scope-creep findings. No source edits performed.

## Open Code Review

首轮审查完成：11 个文件、4 条意见；第二轮两组请求超时，不能宣称完整双轮审查通过。
完整原始报告见 [OCR 首轮报告](ocr-review.txt)。意见正在逐条复现与处置，不盲目重试外部审查。

## Standards 修复复核

Target: `8e926fc..193992e`, `/tmp/dogpdkb-wash-review-fixes`.

#### Documented standards

No remaining violations found in the fix diff. Failed-export discovery now consumes the public mdcompare catalog instead of preserving an undifferentiated raw directory. Resource uncertainty prevents deletion without changing the sync business result. Graphify command evidence uses the actual argv through the existing sanitizer. Signal interruption is rethrown explicitly. No foreign package imports, tool-source changes or runtime algorithm replicas were introduced.

#### Smell baseline

No actionable new smells. The small destination aliases and relative OCR request paths address demonstrated artifact relocation cases; they do not introduce a generic reference/migration framework. Purge relative paths remain confined to the selected wash ownership checks.

#### Verification scope

Read-only targeted diff inspection. CLI graph analysis succeeded against `8e926fc` in the fix worktree (16 changed symbols, risk 0.55); parent-reported focused test evidence was not rerun. No production data or repository files changed.

## Spec 修复复核

Reviewed `8e926fc..193992e` in `/tmp/dogpdkb-wash-review-fixes`. Graph-first CLI analysis succeeded against `8e926fc` (fresh schema-v13 graph).

#### Result

No remaining findings within the targeted review scope.

- Original high finding closed: failed exports use the public mdcompare catalog to retain related pictures, including `../images/` siblings. Unavailable/uncertain catalogs preserve the wash and expose cleanup failure; interruption is propagated.
- Original medium finding closed: actual sanitized Graphify argv is retained in both result JSON and failure rows.
- OCR report findings rechecked: working-directory-relative purge paths resolve correctly; real-mdocr partial outputs relocate original resolved image paths; OCR-facing targets stay relative and remain supported.
- Null resources are explicitly treated as unavailable evidence: cleanup fails nonfatally before pruning, preserves inputs, and leaves the original sync exit code unchanged. This intentionally avoids interpreting missing resources as an empty list.

#### Verification

`pytest -q tests/test_wash_cleanup_cli.py tests/test_wash_durable_inputs.py tests/test_wash_purge_cli.py -k 'failed_export or failed_graph or failed_catalog or real_mdocr or working_directory_relative'`

**13 passed, 33 deselected** in 9.52 seconds. Real mdocr cases unset the OCR token and use local PNGs; no authenticated remote OCR or production deletion was performed.

Scope: targeted fixes only, not a second full-branch review. No source edits.

## OCR 意见处置

- 空资源清单：作为不确定证据报告清理失败，保持原退出码并保留输入，不当作空列表删除。
- 相对 run 路径：支持工作目录相对路径，维持所选 wash 边界。
- 真实 mdocr partial 图片：补充原始已解析路径到持久副本的映射。
- OCR 临时输入：使用相对图片目标，并恢复原资源身份；真实 CLI 验证，未调用远程 OCR。

六条初始意见均已处置；额外补上资源清单阶段的中断传播回归。修正提交为
`4c8cc5a`、`193992e`。双轴定点复核无剩余问题；OCR 第二轮超时限制仍保留，不声称完整双轮通过。
