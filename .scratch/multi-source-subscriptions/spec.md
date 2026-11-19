# Multi-source knowledge subscriptions

Status: ready-for-agent

Dima: [2026111900120771254](https://project.alipay.com/workItem?workItemId=2026111900120771254)

## Problem Statement

A subscription is currently tied to one Yuque book. Markdown maintained outside Yuque cannot join the same knowledge base through normal sync, and operators cannot independently select which inputs to refresh without changing the publication target.

## Solution

Give a subscription an explicit list of stable, typed sources. A subscription owns one corpus, graph and durable state; Yuque and local directory sources supply documents to that shared target. Normal sync scans every enabled source; an explicit subset changes scanning, not publication ownership.

## User Stories

1. As a maintainer, I want a single subscription with Yuque and directory sources, so that our knowledge base includes both without splitting its graph.
2. As a maintainer, I want to attach more than one source of either type, so that I can organize inputs separately from output.
3. As a maintainer, I want directory roots to be explicit, so that a folder named inbox does not become an input just by existing.
4. As a maintainer, I want subscription and source keys to be distinct, so that each source's document identity remains stable across runs.
5. As an operator, I want default sync to scan every enabled source, so that routine operation does not need per-source commands.
6. As an operator, I want to select a subset for a run, so that I can refresh local notes without accessing Yuque and isolate an unavailable source.
7. As an operator, I want invalid source selections to fail, so that typos cannot become a full sync.
8. As an author, I want nested Markdown recognized without a Yuque ID, so that I can drop notes in dated folders.
9. As an author, I want hidden entries, inventory metadata and non-Markdown files excluded as documents, so that staging data is not published accidentally.
10. As an author, I want my source file left untouched, so that the source directory stays under my control.
11. As an operator, I want repeated sync of unchanged Markdown to avoid recompiling it, so that routine scans are cheap.
12. As an operator, I want edited Markdown to use the normal comparison and publication flow, so that the current content is reflected in the shared knowledge base.
13. As an operator, I want source absence and non-selection not to delete published documents, so that partial scans and file moves cannot silently shrink the corpus.
14. As an operator, I want collisions reported against the whole knowledge base, so that an input cannot overwrite another source's output.
15. As an operator, I want scan failures reported before a runnable plan or publication, so that a failed full scan is not silently treated as a successful partial scan.
16. As an operator, I want to inspect a plan without changing production content, so that I can check source scope, accepted documents and conflicts.
17. As an operator, I want resume to use the original source set and byte inputs, so that later edits or a different selector do not alter approved work.
18. As a Yuque subscriber, I want existing Yuque listing, filtering and export guarantees preserved under the new configuration, so that adding folders does not change Yuque sync policy.
19. As an operator, I want a clear error for obsolete configuration, so that I know to upgrade it before enabling the new release.

## Implementation Decisions

- Retain `--book` for subscription selection. New subscription configuration requires a nonempty `sources` list, replaces the legacy top-level Yuque URL and document IDs, and resolves relative paths against the subscriptions file. A source has a unique stable key in the subscription, a `yuque` or `directory` type and an optional enabled flag. Yuque sources own their URL and optional subscribed IDs. Directory sources own their root path.
- `sync` defaults to all enabled sources; repeated `--source KEY` selects only the named enabled sources. `discover` uses the same selection semantics. Invalid, duplicate or disabled source selections fail before dispatch. Resume rejects new selection flags; Graphify recovery stays on its existing frozen-manifest path.
- Use the existing discover/apply/graph seam, not a second sync implementation. Validate all selected scans before publishing a combined differences artifact and committing observations. Distinguish an unscanned source from an empty scan. Report absence as evidence only.
- Namespace durable document identity by source key. Preserve the actual remote ID for Skylark requests; use normalized relative paths and content revisions for directory documents. Do not equate a local hash with a Yuque timestamp. Reject changed source bindings on a frozen resume. Keep observed, raw, corpus and graph facts separate.
- Discover ordinary regular Markdown files recursively, ignoring hidden entries and `_inventory` at every depth. Do not follow symlinks; protect known managed targets from recursive or self-ingestion. Use the source's genuine provenance. Do not export directory documents through Skylark.
- Stage frozen directory input bytes for planning before apply. Reuse the existing published CLI and artifact interfaces of external tools. Report conflicting identity/destination claims in the plan, including claims against already published paths or unselected sources' recorded output, without overwriting either. Preserve existing publication and failure semantics for eligible nonconflicting documents.
- Ensure a stale resumed plan cannot overwrite newer published facts. Edits to live originals after planning do not affect resumed inputs; missing or modified frozen bytes fail validation. Content-based revisions must also permit a genuine revert on a fresh run.
- This release parses only the new configuration shape; no automatic production config or stored-state migration. Update repository-owned examples and CLI docs, not the deployed instance.

## Testing Decisions

- Primary seam: installed `dogpdkb` CLI with an isolated instance, small Yuque listing/export fixtures, directory files, and repo-style substitute executables for expensive or remote steps. Check public output, artifacts, on-disk files and subprocess calls. Existing sync, small-subscription, resume and publication-recovery acceptance cases are prior art.
- Cover full and selected scans; same remote ID in different sources; directory-only sync with no Skylark process; strict configuration and selector errors; nested discovery/exclusions; selected scan failure; repeated unchanged, edited and absent local files; no changes to source files; destination collision with unselected published content; live edit after plan and frozen-input tampering; Yuque behavior under the new config.
- Run repo gates and isolated installed-CLI UAT before opening the PR. Do not use production data, start real downloads or pay for Graphify extraction in this acceptance run.

## Out of Scope

- Actual production config and state cutover: [post-release Dima 2026111900120774857](https://project.alipay.com/workItem?workItemId=2026111900120774857).
- One-time offline Yuque snapshot replay or knowledge-base rebuild: [post-release Dima 2026111900120775016](https://project.alipay.com/workItem?workItemId=2026111900120775016).
- Legacy config compatibility, automatic migration, historical backfill, automatic file deletion, semantic routing and LLM OCR options.

## Further Notes

Dima child links are tracking relationships, not implementation or release gates. Both cutover and replay come after this feature. Only one production instance is currently expected to need migration.
