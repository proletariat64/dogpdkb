# feat: Redesign local help and invocation guidance for all four CLIs

Status: resolved
Type: feature
Created: 2026-11-13

## Problem Statement

People and AI agents cannot reliably discover, construct, and interpret an
invocation using the installed tools' help. dogpdkb, mdcompare, and mdocr expose
command trees but omit essential input, output, configuration, side-effect, and
recovery contracts. Skylark exposes almost identical root and command help and
rejects short help and version requests. mdcompare discards the concrete reason
for an argument error, preventing an agent from correcting its invocation.

Repository documentation alone does not solve this problem: formal use will
start from fresh installations, potentially outside the repository and without
network access. Existing PATH installations are outdated test installations and
are not the acceptance baseline. This work retires the accumulated help debt
across all four tools rather than patching only the audited examples.

## Solution

Provide complete, English-only, progressively disclosed local help for dogpdkb,
mdcompare, mdocr, and Skylark:

1. Root help explains the tool's purpose, command selection, prerequisites for
   first use, and the route to more specific help.
2. Command help contains everything needed to construct a first valid invocation,
   understand its inputs and effects, and interpret its result and next steps.
3. Full help, selected with `--help --full` or `-h --full` at the same command
   scope, exposes the complete installed contract, including schemas and boundary
   behavior, without a checkout, browser, network connection, or authentication.

Help navigation and concrete argument diagnostics become consistent across the
tools. Business exit meanings remain tool-specific and explicit. Recovery advice
explains what a repeated invocation actually does instead of treating every
partial result as a reason to rerun.

## User Stories

1. As a new user, I want root help to state each tool's purpose, so that I can choose the appropriate tool.
2. As an AI agent, I want meaningful command summaries, so that I can select an operation without reading source code.
3. As a new user, I want a short first-use path, so that I can move from installation to a correctly configured invocation.
4. As a user, I want every public command to have its own help, so that the displayed contract matches the selected operation.
5. As a user, I want `-h` and `--help` to work equivalently at every supported scope, so that help navigation is predictable.
6. As a user, I want a successful root `--version` request, so that I can identify the installed artifact without running work.
7. As an offline user, I want full contracts installed with each tool, so that help remains available outside its source repository.
8. As an AI agent, I want ordinary help to include the essentials for a first invocation, so that I do not need to retrieve full documentation before every call.
9. As an advanced user, I want `--help --full` and `-h --full` to expose the complete contract for the selected scope, so that I can inspect edge cases locally.
10. As a user, I want all generated help and argument guidance in English, so that terminology and examples remain consistent.
11. As a user, I want required inputs, optional arguments, accepted values, units, and defaults explained, so that I can construct valid arguments.
12. As a user, I want relative-path bases explained separately for command arguments and manifest fields, so that I can run from any working directory.
13. As a dogpdkb operator, I want configuration selection and precedence explained, so that I know which instance my command will affect.
14. As a dogpdkb operator, I want book-key selection and multi-source rules explained, so that I select the intended source.
15. As a dogpdkb operator, I want the roles of discover, apply, update-graph, and sync explained, so that I choose the required stage and understand its write scope.
16. As a dogpdkb operator, I want existing-corpus initialization explained, so that I register facts without accidentally rewriting corpus or graph content.
17. As a dogpdkb operator, I want plan pauses, run selection, and resume eligibility explained, so that I continue eligible work correctly.
18. As a dogpdkb operator, I want status snapshot, watch, and JSONL behavior explained, so that I can observe work with the correct output parser.
19. As a dogpdkb operator, I want automatic wash cleanup and manual purge boundaries explained, so that I know which evidence is retained or removed.
20. As a dogpdkb operator, I want publication and lifecycle commands to describe their effects and prerequisites, so that I can distinguish them from synchronization.
21. As an mdcompare user, I want help to identify manifest inputs as JSON, so that I do not pass Markdown files where manifests are required.
22. As an mdcompare user, I want valid minimal manifest examples, so that I can construct catalog and comparison inputs without guessing fields.
23. As an mdcompare user, I want catalog, knowledge, and compare boundaries explained, so that I choose body-only comparison or resource and OCR-evidence comparison correctly.
24. As an mdcompare user, I want specific argument errors alongside stable error identifiers, so that I can repair an invalid invocation.
25. As an mdcompare user, I want unresolved and review results explained using report fields and reasons, so that I can assess them without blindly repeating the command.
26. As an mdocr user, I want prepare, ocr, and batch distinctions explained, so that I know when preparation, downloading, or model calls can occur.
27. As an mdocr user, I want Prepared image and OCR block terminology used consistently, so that help agrees with the domain glossary.
28. As an mdocr batch user, I want a complete minimal manifest and resource-selection rules, so that I can distinguish omitted selection from an empty selection.
29. As an mdocr user, I want authentication and gateway prerequisites explained, so that I know which operations require a token and which can reuse existing results.
30. As an mdocr user, I want budgets, output paths, cache reuse, and force behavior explained per command, so that I can predict repeated work and costs.
31. As an mdocr batch user, I want partial resource and document outcomes explained, so that I can retain usable Markdown and inspect failures.
32. As a Skylark user, I want distinct listing and export help, so that I can discover their different requests, outputs, and remote operations.
33. As a Skylark user, I want request schemas, document formats, and path rules explained, so that I can construct a valid export request.
34. As a Skylark user, I want gateway environment and SSO prerequisites documented locally, so that I can configure the existing authentication flow.
35. As a Skylark user, I want listing completeness and export version limitations explained, so that I do not mistake missing or uncertain evidence for success.
36. As an automation author, I want stdout, stderr, report files, and exit codes described separately, so that I parse the right result channel.
37. As an automation author, I want each non-success outcome to identify usable artifacts and an appropriate next action, so that I do not trigger an unsafe or ineffective retry.
38. As an operator, I want repeated-invocation behavior explained, so that I know about reuse, overwrites, downloads, and possible model calls before acting.
39. As an unauthenticated user, I want help and version requests to work without configuration, credentials, or operational dependencies, so that discovery does not require a working deployment.
40. As an operator, I want help requests to avoid locks, runtime-state initialization, tool launches, and remote access, so that reading documentation does not affect active work.
41. As a maintainer, I want fresh-install acceptance through the real executable entry points, so that source-only success cannot hide missing packaged contracts.
42. As a maintainer, I want coverage based on the current public command inventory, so that newly added commands cannot escape help acceptance.

## Implementation Decisions

1. Treat help as a complete product surface across all four tools. Include every
   public root and command present at delivery, including dogpdkb observation,
   purge, initialization, preflight, publication, and lifecycle commands. The
   historical audit inventory is evidence, not a fixed coverage limit.
2. Retain the existing Python argparse foundation. Improve descriptions,
   arguments, grouping, examples, and scoped epilogs; add only the routing and
   packaged contract support needed for ordinary and full local help. Do not
   introduce a general help platform or require a new framework.
3. Rework Skylark's own independent CLI help and build artifacts. Its help must
   describe listing and export separately and support the agreed help and root
   version flags. Keep its runtime and distribution independent from dogpdkb.
4. Ordinary root help must contain a purpose, meaningful command summaries, a
   minimal starting path, and explicit navigation to command and full help.
   Ordinary leaf help must independently satisfy first-invocation needs; full
   help must not be required to discover an essential argument or prerequisite.
5. Provide `--help --full` and `-h --full` at every supported help scope, returning
   success. Full leaf help includes the ordinary contract plus complete input
   and output schemas, required and optional fields, accepted values, null and
   omitted-field meanings, path resolution, limits, and boundary behavior.
   Root full help explains tool-wide contracts and how to reach each command's
   full contract; recursive concatenation of all commands is not required.
   `--full` is a help modifier, not a business execution mode; without a help
   flag it produces a concrete usage diagnostic before execution.
6. Ship the full local contract in each tool's installable artifact. Rendering
   must not depend on a checkout, repository-relative documentation, a remote
   URL, a pager, or an external documentation tool. Existing documentation can
   remain useful background, but it is not the only route to required content.
7. Make normal and full help agree about argument names, requiredness, defaults,
   schemas, and outcome semantics. Keep packaging and content ownership simple;
   do not duplicate a tool's behavioral algorithms in documentation machinery.
   Help and new argument guidance are English-only; source titles and user
   content need not be translated.
8. Every leaf contract must identify purpose, inputs, requiredness, defaults and
   units, path bases, prerequisites, minimal invocation and required JSON,
   stdout/stderr behavior, disk artifacts, overwrite and preservation rules,
   side effects, exit meanings, and next steps. Explicitly state when a topic
   does not apply. Use the correct full command path in usage and examples.
9. Document dogpdkb configuration precedence and the distinction between source
   and installed defaults. Explain book keys, offline listing inputs, observed,
   raw, corpus, and graph facts, run selection, pause and resume semantics,
   durable failure suppression, and publication separation according to the
   current public contracts. Explain current wash retention and purge behavior;
   purge must not be presented as resetting suppression or triggering retries.
10. Document mdcompare manifest types and complete minimal examples for catalog,
    knowledge, and compare. Distinguish body-only comparison from image/resource
    and declared OCR-evidence comparison, resolved from unresolved results, and
    report publication from modification of source documents.
11. Document mdocr prepare, ocr, and batch independently. Use Prepared image and
    OCR block terminology. Explain manifest identity, uniqueness, resource
    selection, token requirements, cache reuse, optional downloads, endpoint
    selection, time budgets, force behavior, reports, and partial outcomes.
    Do not apply batch exits or token rules indiscriminately to other commands.
12. Document Skylark gateway environment and SSO prerequisites, listing and
    export schemas, supported source formats, actual export versions,
    completeness limitations, atomic publication, and per-document failures.
    Make clear that listing does not fetch document bodies and export does not
    download images or perform OCR. Keep scheduling and corpus policy outside
    Skylark.
13. Make usage errors actionable: identify the command and concrete missing,
    unknown, or invalid argument and point to that command's help. Preserve
    stable error identifiers such as mdcompare's INVALID_ARGUMENTS while adding
    the discarded detail. Report diagnostics on stderr rather than corrupting
    structured result stdout. Cover tool-specific schema validation errors
    where they belong to the public invocation contract without exposing secrets.
14. Preserve existing tool-specific business exit meanings and public artifact
    schemas. Explicitly fix discrepancies with the intended usage contract,
    including Skylark's invalid CLI usage exit of 64. Do not renumber exits to
    make all tools identical. Explain that mdocr usage errors and batch partial
    outcomes can both exit 2 and must be distinguished by invocation diagnostics
    and the presence and contents of the structured report.
15. Explain next actions per outcome, including the usable artifact, diagnostic
    location, repeat behavior, and whether resume is actually supported.
    A terminal dogpdkb partial result is not an instruction to resume that run;
    respect durable same-version failure/unknown suppression and the existing
    external-agent handling policy. Do not promise that a new invocation alone
    retries suppressed failures. Describe other tools' rerun behavior separately.
16. Route help and root version requests before configuration resolution,
    authentication, runtime locks, state/cache creation, operational dependency
    probes, or subprocess launches. Missing configuration, tokens, gateway
    connectivity, or optional business tools must not prevent documentation
    access. Preserve command-specific options such as the lifecycle update
    target version; root version discovery must not hijack them.
17. Preserve the public-CLI orchestration boundary. No cross-tool private imports,
    helper scripts importing private tool internals, remote credential changes,
    Graphify/Yuque source changes, or embedded external runtimes are needed.
    This is a help and invocation-guidance redesign, not a rewrite of tool
    algorithms, synchronization policy, state, or automatic retry behavior.

## Testing Decisions

1. Use the installed public CLI subprocess boundary as the primary acceptance
   seam for all four tools. Observe argv, stdout, stderr, exit status, artifacts,
   and external activity. The user has already accepted fresh-install CLI
   verification without real synchronization or model calls; no new lower-level
   acceptance seam needs approval.
2. Prefer existing prior art: mdocr's direct CLI help/diagnostic tests, the
   platform's disposable wheel-install smoke harness, mdcompare's public command
   tests and real local manifest fixtures, and Skylark's subprocess tests with a
   local fake MCP proxy. Extend these where appropriate. Avoid testing parser
   implementation details or asserting an entire help transcript byte for byte.
3. Establish the current public command inventory at implementation time and
   verify every root and command. Require `-h`, `--help`, `-h --full`, and
   `--help --full` to succeed at every scope and root `--version` to succeed for
   each tool. Root, command, and full output must disclose the corresponding
   scope, not the same generic placeholder text.
4. Assert contractual content rather than particular line wrapping: correct
   command paths, meaningful purposes, each argument's semantics and actual
   defaults, prerequisites, input types and path rules, minimal valid examples,
   output and write scope, exits, and recovery guidance. Check English prose and
   equivalence of short and long help flags.
5. Verify missing arguments, unknown commands/options, invalid values, and the
   unsupported standalone full modifier through safe invocation paths. Assert
   concrete repairable detail, the right help hint, stderr routing, preserved
   stable identifiers, and intended tool-specific exit codes. Invalid invocations
   must stop before business work or destructive action.
6. Build fresh artifacts and install them into disposable environments. Run from
   an unrelated working directory with source-path overrides removed; verify
   the launched executable and imported distribution originate in the fresh
   installation. Install and test Skylark's actual built CLI independently;
   a placeholder in the dogpdkb harness is not Skylark acceptance.
7. Prove the local full contracts are included in the installable distributions
   and remain readable when checkout paths and network access are unavailable.
   Inspect distribution contents as well as running the installed commands.
   Confirm all three Python executable entry points and the independent Skylark
   entry point expose the delivered behavior.
8. Exercise help and version requests with missing and invalid configuration,
   absent token/SSO settings, and unavailable optional business tools. Test
   isolated plain and managed-install contexts so runtime lifecycle locks cannot
   accidentally gate help. These requests must return success without invoking
   the tools described by the documentation.
9. Observe relevant filesystem and process/network activity around help/version
   requests. Verify no business state, lock, cache, report, corpus, or graph is
   created or mutated, and no gateway, SSO, model, extraction, download, or
   operational tool subprocess is started. Check isolated HOME/config/cache and
   instance paths as well as cwd. Do not infer global absence of side effects
   solely from an unchanged cwd.
10. Validate documented minimal JSON against actual public schema behavior using
    real local Markdown and small synthetic image fixtures where needed.
    Confirm path bases from a different cwd. For remote-only operations use the
    existing local fake gateway/proxy and safe output directories. No live
    export, OCR model call, extraction, or production corpus is required.
11. Verify representative resolved, unresolved/review, partial, invalid-usage, and
    failure results agree with the documented report and exit contracts. Use
    isolated fixtures and existing fake services; check usable artifact
    preservation and distinguish mdocr usage exit 2 from batch partial exit 2.
    Verify advice does not claim blanket retry safety or a nonexistent resume
    operation. Reuse existing behavioral tests rather than duplicating tool
    algorithms in new test expectations.
12. Include regressions for the audited defects: discarded mdcompare diagnostics,
    missing manifest examples and mdocr batch argument semantics, identical
    Skylark command help, Skylark short-help/version failures and usage-exit
    mismatch, incomplete dogpdkb configuration/resume guidance, incorrect
    publication usage prefix, and prepare help claiming re-OCR behavior.
    Include commands added since the audit, notably purge.
13. After OCR remediation, run focused CLI regressions locally only. Per the
    user's explicit delivery instruction, full make check and make smoke gates
    run in PR CI, without equivalent local script workarounds. Record actual versions,
    command counts, pass/fail results, and evidence limitations. Keep source
    tests, fresh-install acceptance, CI, host deployment, and production/model
    acceptance separate. Do not broaden into live services after these gates
    pass.

## Out of Scope

- Implementing the feature during this specification-publication task.
- Upgrading, repairing, migrating, or validating the old PATH test installations.
- Releasing, tagging, publishing packages, deploying software or websites, or
  replacing an active user installation as part of help acceptance.
- Changing business algorithms, public manifest/report schemas, synchronization
  stage policy, cache policy, durable failure suppression, retry policy, or
  recovery eligibility in order to make the help easier to describe.
- Standardizing business exit numbers across tools or silently changing all
  argument failures to a new common exit code.
- A generic help framework, machine-readable help schema, recursive help dump,
  new positional help command, documentation server, or automatic translation.
- Broad translation of unrelated historical repository documents.
- Modifying Graphify/Yuque sources or bundling their or Skylark's runtime into
  the Python orchestrator.
- Real synchronization, live gateway authentication, remote document export,
  paid OCR/model calls, Graphify extraction/rebuild, corpus backfill, production
  cleanup, or production-data acceptance.

## Further Notes

- This spec synthesizes the settled grilling decisions: complete help redesign;
  self-sufficient first-invocation help; English-only output; local offline full
  contracts; consistent flags and actionable diagnostics; tool-specific exit
  semantics; and explicit next actions and repeat-call effects. Fresh installs
  are the verification target; outdated test installs need no repair.
- The [CLI help audit](../cli-help-audit/review.md) is historical evidence. Its
  baseline covered 23 root/command help paths and predates the current purge
  command. Do not reuse its counts as proof of current completeness. Its cwd-only
  side-effect observation was deliberately limited, and it did not prove absence
  of HOME writes or network access.
- Current repository context was inspected on 2026-11-13 at source commit
  `e8a88c45d59f4c99d4bf7ae03cca0439761e75c5`. The restored independent Skylark
  baseline is `0e7c21883481d04a42a771c6fad1ddd5e6e0aa91`. Recheck the intended
  source checkout and build before implementation; a temporary restored build
  alone is not a durable delivery destination.
- Authoritative starting points are the [platform and installation boundary](../../docs/platform.md),
  [current operating behavior](../../README.md),
  [independent tool contracts](../../docs/orchestrator-tool-contracts.md),
  [Skylark contract](../../docs/orchestrator-skylark-contract.md),
  [mdcompare contract](../../docs/mdcompare.md), and
  [mdocr contract](../../docs/spec/mdocr.md). Use the current domain glossary and
  command behavior to resolve stale descriptions; do not silently change the
  business contract to match an old document.
- Publication is this local tracker specification with `Status: resolved`.
  Implementation tickets, commits, reviews, CI, release, and deployment are
  separate actions and are not claimed by publishing the spec.

- Implementation delivery: [dogpdkb PR #20](https://github.com/proletariat64/dogpdkb/pull/20).
  All seven local implementation tickets are resolved. Independent Skylark
  source/build/patch are complete; its separate remote push was not authorized
  by automatic review and remains pending explicit destination authorization.
