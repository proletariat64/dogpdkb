# Skylark help implementation and acceptance

Status: implemented in a durable local source branch; awaiting integration review
Date: 2026-11-13

## Provenance and preservation

The source baseline is the restored independent Skylark commit
`0e7c21883481d04a42a771c6fad1ddd5e6e0aa91`. Direct byte comparisons confirm
its CLI, extension, package metadata, and CLI test match the recorded commit.
The restored directory remains unchanged. Work is isolated under
`/tmp/skylark-help-redesign`, with dependencies reused through a node_modules
symlink; no dependency download was needed.

The original `/home/ubuntu/dev/pi_skylark` checkout remains on main at
`bff715483d19a0e470259abcb932d2369158e6b5`, with its preexisting modifications
to index.ts and three test files preserved. Its GitHub default branch is main.
No source changes, commits, pushes, PRs, or releases were made in that checkout.
A separate linked worktree now holds the durable source commit described below.

## Delivered changes

- [Binary-safe source patch](skylark-help.patch): CLI routing, bundled English
  ordinary/full contracts, packaging selection, documentation, and tests. Apply
  against the restored commit using `git apply --unidiff-zero`, then run
  `npm run build`. The patch deliberately excludes generated dist/cli.mjs so it
  applies both to the exact Git source and the restored snapshot that already
  contains that executable. The durable commit and npm package include the
  rebuilt executable. Do not apply the patch to the dirty main tree.
- Implementation worktree: `/tmp/skylark-help-delivery` (removed after validation;
  the durable local branch and commit remain).
- Durable Git branch: `feat/cli-local-help` in the original Skylark repository.
- Implementation commit: `0dbb2cc904ce257596252b377769d94ec6811cc1`.
- Exact parent: `0e7c21883481d04a42a771c6fad1ddd5e6e0aa91` on the restored
  `feat/orchestrator-export-cli` lineage. The default branch remains main.
- Initial source/build workspace: `/tmp/skylark-help-redesign`.
- Installable npm artifact:
  `/tmp/skylark-help-redesign/artifacts/pi-skylark-0.1.0.tgz`.
- Fresh installed command:
  `/tmp/skylark-help-install/node_modules/.bin/skylark`.
- npm package contains seven intentional entries, including the executable and
  full contract source. It excludes tests, graph data, and node_modules. The
  executable bundles the contracts and requires no runtime source/document lookup.

## Red and green evidence

The first public subprocess regression failed because `listing -h` returned 1
with an unknown-option diagnostic. It now succeeds with listing-specific help.
The next regression failed because export full help lacked a full contract;
it now exposes complete request/result fields and boundary behavior.

Final commands passed:

- `make test`: bootstrap preservation suite; all 8 transport scenarios; the
  independent CLI suite; and all 30 help/version/usage tests.
- `npm run build`: rebuilt the standalone executable.
- Offline-cache `npm pack`: produced the seven-entry local package.
- Offline `npm install --ignore-scripts --omit=dev` into a disposable prefix:
  installed one local package without obtaining dependencies.
- All 12 root/listing/export short, long, and full-help paths plus root version
  passed through that freshly installed command from an unrelated cwd.
- The help matrix used a poison proxy and isolated HOME/config/cache/cwd paths;
  no proxy launch or new files were observed. Invalid gateway values and absent
  SSO/config did not block help. No live gateway, SSO, model call, extraction, or
  production-data operation ran. This is behavioral observation, not a kernel
  network-trace claim.
- Public help's minimal request JSON was parsed and executed through the local
  fake MCP proxy with real Markdown outputs. Relative document paths resolved
  from cwd rather than the request directory. Partial export retained an old
  failed target unchanged; empty requests completed with zero counts.
- `git diff --check` and `git apply --unidiff-zero --check` against the unchanged
  restored snapshot passed for the final six-file source patch. Reverse-checking
  the patch against the durable implementation also passed. In the durable Git
  worktree the original commit has no tracked dist executable; `npm run build`
  regenerated the separately committed executable. Omitting that generated file
  prevents an already-existing-dist error when applying to the restored snapshot.
- The durable source worktree independently reran `npm run build` and `make test`
  successfully before committing its exact seven implementation files. Its graph
  was refreshed and changes analyzed against the actual default `origin/main`.
  Static graph test gaps do not model the public subprocess assertions and are
  not presented as missing behavioral acceptance.

## Behavior and limitations

All invalid CLI usage now exits 64 with concrete stderr detail and scoped help
navigation. Business exit meanings and output/report schemas are unchanged.
Unknown non-table format values still follow the existing Markdown fallback;
full help documents that boundary rather than introducing validation changes.

A repeated export refetches every selected source and replaces each successful
output; it has no CLI result cache or resume mode. Failed sources can leave old
files, and a later storage failure can leave successful earlier outputs without
a new report. Full help describes these boundaries explicitly.

The implementation is persisted in the independent repository's local branch,
not only a temporary patch. It has not been pushed, merged upstream, or released.
The branch has no upstream tracking configuration. A future PR to main includes
three commits: the existing standalone CLI commit, its listing-total fix, and
this help commit. A PR to the existing restored feature branch would include
only the help commit; the parent integration task owns that remote decision.
Do not claim the dogpdkb PR alone updates Skylark's upstream main or its installed
user command. The original dirty user checkout was intentionally left untouched.

## Publication authorization

An attempted push of feat/cli-local-help to
https://github.com/proletariat64/pi_skylark was rejected before execution by
automatic approval review: trusted instructions did not explicitly authorize
that separate repository destination. The local branch, commit, package and
source patch remain available. Destination-specific authorization has been
requested; no workaround or alternate export was attempted for that push.
The dogpdkb branch/PR remains the explicitly requested primary delivery.

The linked implementation worktree has been cleaned. The durable feature branch,
source commit, package, source patch and original dirty main checkout remain.
