# dogpdkb — 全球资金平台产品 knowledge corpus

This repo contains the installable `dogpdkb` CLI orchestrator, independent
`mdcompare` and `mdocr` CLIs, and a Graphify-built Yuque knowledge corpus with
an independent Web dashboard. Large corpus/graph artifacts are machine-local
and intentionally gitignored. Current CLI contracts and development gates are
in `README.md`; implementation acceptance is under `.scratch/`.

## What lives here

| Path | Tracked? | What it is |
| --- | --- | --- |
| `src/dogpdkb/` | **yes** | Serial CLI orchestration, durable facts, read-only status, independent Web/lifecycle |
| `src/mdcompare/`, `src/mdocr/` | **yes** | Independent tools; orchestrator consumes their public CLIs/artifacts |
| `graphify_flatten_ready/*.md` | **no** (gitignored `graphify*/`) | Historical baseline: 4,584 raw docs, ~106 MB, exported from Yuque book "全球资金平台产品" (`roots.json`); measure current counts when needed |
| `graphify_flatten_ready/graphify-out/graph.json` | **no** | Historical baseline: **34,661 nodes / 47,509 edges**, ~36 MB, built via Graphify. Machine-local — do NOT commit or rebuild casually. |
| `graphify_flatten_ready/graphify-out/` (rest) | **no** | `GRAPH_REPORT.md`, `manifest.json`, `cache/`, `memory/`, `reflections/`, `.graphify_analysis.json`, `.graphify_labels.json` |
| `scripts/graphify-watchdog-uat.py` | **yes** | Monitor that watches a graphify extraction run (snapshot/hourly checks) |
| `snapshot/` | **no** | Watchdog runtime output (`snapshot.md`, `hourly-checks.log`) |
| `tmp/` | **no** | Scratch: handoff docs, ad-hoc research (`graphify-extraction-handoff-20260818.md`, `标准付款产品记账与业务含义研究报告.md`) |
| `roots.json`, `roots.md` | **yes** | Origin: `https://yuque.antfin.com/global-fund-platform/gom7xx` |
| `wash/`, `inbox/` | **yes** | Empty staging dirs |

Code, tests and engineering scripts are version controlled. Corpus, graph,
cache, credentials and run artifacts remain outside commits.

## Hard boundary: orchestrate public CLIs

`discover`, `apply`, `update-graph` and `sync` form one serial implementation.
The orchestrator owns selection, plans, actual version/step results and policy;
Skylark, mdcompare, mdocr and Graphify own their tool algorithms and runtime.

- In `src/dogpdkb/`, call tools only through subprocess argv and documented
  JSON/Markdown artifacts. Importing tool packages, private symbols or invoking
  those imports through a helper script is forbidden, including `mdocr.config`.
- Graphify and Yuque are external tools: never change their source or require a fork.
  Only user-owned tools may receive authorized CLI extensions. Keep
  comparison/OCR/cache/graph extraction and merging inside their tools.
- Run `init-state` before using an existing corpus. It registers facts without
  rewriting Markdown/graph or importing old runs/state. Preserve separate
  observed/raw/corpus/graph truth and same-version failure/unknown suppression.
- Keep Web publication separate from sync. Completed/partial sync cleans only
  its own wash, retaining failed Markdown/images/reports. Manual purge deletes
  selected wash only; preserve durable state, inputs and tool caches. Historical
  failure handling stays with an external agent. Explicit `--retry-graph`
  recovery revalidates the saved batch; add no automatic migration or retry layers.

When changing an integration, read `docs/orchestrator-tool-contracts.md`,
`docs/orchestrator-skylark-contract.md` or `docs/orchestrator-graphify-contract.md`
for that tool. Shared state/discover interfaces are in
`docs/orchestrator-core-contracts.md` and `docs/orchestrator-discover-contract.md`.

## The graph artifact

`graphify_flatten_ready/graphify-out/graph.json` — networkx `node_link_graph`
format. Top-level keys: `directed, multigraph, graph, nodes, links, hyperedges,
built_at_commit`.

- **Node fields:** `label, file_type, source_file, source_location, source_url,
  captured_at, author, contributor, rationale, community, norm_label, id,
  community_name`. `id` is a free-form slug (e.g. `paper_11fs_baas`), **not** the
  `type:relative-path` convention U-A expects.
- **Edge fields:** `relation, confidence, confidence_score, source_file,
  source_location, weight, source_url, captured_at, author, contributor,
  rationale, source, target`. Edges use `source`/`target` and a free-form
  `relation` string; U-A expects `type` (from a fixed 26-type enum) + `direction`
  - `weight` 0-1.
- Communities are **already clustered and named** (3,613 communities,
  `.graphify_labels.json` has `community_name`). There are no U-A "layers" or
  "tour" arrays.

### Cost to rebuild — do not redo

Full extraction already ran to completion: **30.98M input / 17.01M output tokens**
(DeepSeek v4 flash, ~17 hrs, pause/resumed around peak-pricing windows). Final
quality: `failed=0`, a few hyperedges dropped on truncated JSON (~1–2% chunks).
See `tmp/graphify-extraction-handoff-20260818.md` for the full run history and
operational gotchas.

## Planned direction: Understand-Anything front shell

Goal: render this corpus through the **Understand-Anything** (U-A) interactive
dashboard (github `Egonex-AI/Understand-Anything`, ~79k★, MIT, a Claude-Code
plugin with `--knowledge` mode for Karpathy-style wikis). U-A reads a
`.ua/knowledge-graph.json` — **a different schema from graphify's**. The core
effort is schema translation, not re-analysis.

U-A `KnowledgeGraph` schema (target) — authoritative in
`understand-anything-plugin/skills/understand/SKILL.md`:

```typescript
interface KnowledgeGraph {
  version: string;          // "1.0.0"
  project: { name, languages[], frameworks[], description, analyzedAt, gitCommitHash };
  nodes: GraphNode[];        // every node: id, type, name, summary, tags[], complexity
  edges: GraphEdge[];        // every edge: source, target, type, direction, weight
  layers: Layer[];           // id, name, description, nodeIds[]
  tour: TourStep[];          // order, title, description, nodeIds[]
}
// 13 node types: file function class module concept config document service table endpoint pipeline schema resource
// 26 edge types: imports exports contains inherits implements | calls subscribes publishes middleware | reads_from writes_to transforms validates | depends_on tested_by configures | related similar_to | deploys serves provisions triggers | migrates documents routes defines_schema
```

U-A's inline validator (Phase 6) hard-fails a graph missing `type, name, summary,
tags` on nodes or `type/direction` on edges, and requires every file-level node
in a layer. A direct graphify→U-A transform must synthesize all of those.

## Conventions for agents working here

- The `yuque` CLI supports listing and downloading Yuque resources. Sync uses the restored Skylark CLI.
- Sync recent deltas only; historical missing docs were intentionally filtered—do not backfill.

1. **Fast path for corpus questions:** if `graphify_flatten_ready/graphify-out/graph.json`
   exists and the user asks a natural-language question about the corpus, use
   `graphify query "<question>"` — do not recompile. (See `~/.pi/agent/skills/graphify/SKILL.md`.)
2. **No network reclone of U-A on every turn.** Inspect via the GitHub tool /
   ctx7 (`npx ctx7@latest docs /egonex-ai/understand-anything "..."`) instead of
   cloning unless a full source tree is needed.
3. **Secrets in corpus md are outdated/dummy** — opengrep flags ~11 files. Do not
   scrub, do not flag, do not paste contents into logs/docs.
4. **`graphify*/`, `snapshot/`, `.pi/` are gitignored on purpose.** If `git status`
   shows them, the `.gitignore` is broken — fix the ignore, don't `git add`.
5. **Code relationships:** use code-review-graph first for symbols, dependencies,
   tests and impact, with the current worktree as `repo_root`. Refresh incomplete
   or stale coverage; analyze impact before substantial edits and actual default
   branch changes before commits/review. Keep `.code-review-graph/` uncommitted.
   Use `rg` for literals/non-code or graph gaps.
6. **Never run `pkill -f <pattern>` from a `bash -c` containing that pattern** —
   it matches and kills the invoking shell. Kill by numeric PID via `pgrep -f`
   first. (Bitten 3× during the extraction run.)

## Inherits

Global pi rules in `~/.pi/agent/AGENTS.md` and `~/AGENTS.md` (caveman chat style,
conventional commit prefixes, pi-lens as primary linter). Those still apply except
where this file narrows them.

## Agent skills

### Issue tracker

Issues and specs are tracked as local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the five canonical default labels. See `docs/agents/triage-labels.md`.

### Domain docs

Domain documentation uses the single-context layout. See `docs/agents/domain.md`.
