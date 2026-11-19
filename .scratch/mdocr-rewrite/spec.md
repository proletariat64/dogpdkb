# Rewrite mdocr for Paddle and Pi-backed multimodal OCR

Status: ready-for-agent

## Problem Statement

The operator needs to convert Markdown with embedded images into Markdown that preserves the authored document and makes useful image content searchable. Paddle OCR can transcribe text, but it does not reliably recover diagrams, tree structure, or the meaning of slides. A separate Terra pilot recovered useful structure, but was not a supported CLI, and large images exposed an unreliable token estimator. The operator needs one maintainable tool with deterministic inputs, observable scheduling and safe recovery before deciding whether to use LLM processing for a full corpus rebuild.

## Solution

Rewrite `mdocr` around two explicit modes, `paddle` and `llm`, and one plan/execution contract. Both modes publish Markdown with a traceable OCR block in place of each successfully processed image reference. Paddle remains usable without Node or Pi. LLM mode uses the installed Pi SDK's model and OAuth runtime, submitting the entire document and its image inputs and receiving strict, typed image patches. The editable prompt describes how to transform each kind of image; the tool owns the output schema, validation, publication, and recovery. This is a new contract, not a migration.

## User Stories

1. As an operator, I want to choose Paddle or LLM explicitly, so I know which system will see my inputs.
2. As a Paddle user, I want my configured Paddle endpoint and credentials to work without installing Pi or Node, so the base tool remains independent.
3. As an LLM user, I want to log into my supported OpenAI OAuth provider in Pi once, so I do not maintain a second credential store.
4. As a headless operator, I want to use the provider's Device Code option in Pi, so `mdocr` can use the resulting session without a local browser callback.
5. As an LLM user, I want to pin a model as `provider/model`, so the model's route is unambiguous.
6. As an LLM user, I want Terra with medium effort by default and a supported effort override, so I can tune quality and throughput explicitly.
7. As an operator, I want missing Pi, incompatible Node/SDK, missing OAuth, and incompatible models to fail before any inference, so a job never silently switches credentials or models.
8. As an LLM user, I want to select an external prompt file, so I can tune conversion behavior without a code change.
9. As a runner, I want a versioned response schema independent of the prompt, so a prompt change cannot change the JSON contract.
10. As an author, I want one input Markdown file to produce one specified output file without changing the source, so I can use the tool on a single document.
11. As an operator, I want to submit a JSON list of Markdown file paths, so I can run an exact subset.
12. As an operator, I want to submit a directory of Markdown files recursively, so I can process a whole tree without building the list myself.
13. As a runner, I want deterministic input-to-output mappings and preflight rejection of collisions, so one document cannot overwrite another or an input.
14. As a runner, I want a machine-readable plan before execution, so I can inspect and control the work before any inference.
15. As a runner, I want a machine-readable checkpoint/result list with document, image, and request outcomes, so I can distinguish successful, held, failed, unattempted, and unknown work.
16. As an LLM user, I want a whole document and all selected images considered together, so captions and nearby context can inform each image patch.
17. As an auditor, I want image patches bound to exact occurrences in the source, so repeated images do not get misplaced or silently omitted.
18. As an author, I want recognized images replaced by marked OCR blocks with their source links, so inline knowledge is searchable and I can verify it against the original.
19. As an author, I want all text outside the replaced image references kept unchanged, so OCR does not rewrite authored material.
20. As a reader, I want data-rich table images converted to Markdown tables, with meaningful headers, units, cells, and row/column associations preserved, so I can read and check the data.
21. As a reader, I want flowcharts and mind maps converted to validated Mermaid, so I can read the original structure and direction rather than an unordered OCR dump.
22. As a reader, I want chart and explanatory slide content as separate visible transcription, summary, and translation, so I can distinguish evidence from interpretation.
23. As a reader, I want other image types summarized as text, so the document remains useful without forcing an inappropriate diagram or table.
24. As a reader, I want unreadable text and uncertain relations marked and the original link retained, so they are not mistaken for reliable extracted facts.
25. As a runner, I want normal batches to begin with three concurrent conversion jobs, so throughput is better than serial execution.
26. As a runner, I want rate limiting to reduce future concurrency before stopping admission, so I do not compound provider throttling or send the same request twice.
27. As an LLM operator, I want optional independent token and request ceilings, unlimited by default, so batch resource limits are under my control.
28. As an LLM operator, I want ceilings to be enforced across concurrent admission and resume, so a restart or late response cannot make the tool spend the same allowance again.
29. As an operator, I want already verified results reused and unknown requests surfaced rather than automatically replayed, so recovery does not create duplicate jobs or charges.
30. As a reader, I want only complete documents published to their planned destination, so an interrupted or held document cannot be mistaken for finished.
31. As a `dogpdkb` operator, I want the orchestrator to invoke the new public CLI and read new status/evidence artifacts, so it stays independent of the OCR implementation.
32. As a `mdcompare` user, I want authored comparison to recognize the new OCR block boundaries and evidence, so added OCR text is not confused with authored changes.
33. As a maintainer, I want no legacy format shims, so an old plan is not silently interpreted under new execution or safety rules.

## Implementation Decisions

### Model and provider boundary

- Keep the Python CLI and Paddle backend; use a small Node bridge with the **installed Pi SDK's model runtime** only in LLM mode. Resolve the supported installed SDK without machine-specific paths. Do not start an agent session or load project tools, extensions, agent instructions, or a chat history. Document the supported Pi/Node versions. Validate dependencies lazily: Paddle, planning where no inference is needed, and basic help must not require a working LLM login.
- Login happens in Pi. Share Pi's effective agent directory, model configuration/catalog and OAuth credential store, including the supported agent-directory override. Let Pi handle refresh and its credential writes. No separate `mdocr` OAuth login, copied secrets, ambient API-key fallback, automatic provider fallback, or implicit model substitution.
- First-release provider allowlist: OpenAI via Pi's `openai` and `openai-codex` OAuth routes. Model identifiers are explicit `provider/model` values; default is `openai/gpt-6.1-terra`, effort `medium`. Check provider identity, OAuth availability, image input, and effort support before inference. Device Code login is selected in Pi on its `openai-codex` route; an operator using that route names it explicitly. Pi listing a model is not equivalent to having tested its vision endpoint, route, or structured-output behavior.
- Use the selected external prompt file or the default packaged prompt. Freeze its exact effective content or verified content identity in the plan. Maintain a tool-owned, versioned strict JSON Schema independent of prompt text. The request carries model, effort, complete authored Markdown with stable image occurrence IDs, and original local/explicitly resolved image bytes. Bind every returned patch to its planned occurrence; reject missing, duplicate, unexpected or misordered IDs, invalid kinds, malformed payloads, truncation and provider failures. Distinguish an explicit hold with a reason from successful conversion. No automatic cropping or unreported transformations. Check context limits separately from run budgets.

### Plan, execution, and budgets

- Expose a public plan-only operation and an explicit operation to run/resume that plan. A convenience single invocation may share that implementation, not bypass it. Accept exactly one source shape: a single Markdown path, a JSON array of Markdown paths (relative entries are relative to the list), or a recursive Markdown directory. Single maps to a single output Markdown file; list and directory map to a distinct output directory and ordered result list. Preserve the input directory's relative tree for directory mode; for all modes record the mapping rather than relying on a consumer to infer it. Reject duplicates, output collisions and source/output overlap before execution. A planned output subtree must not be rediscovered as input.
- Machine-readable plan and result artifacts are separate: the plan is immutable, versioned, and binds source and image identities, mode, resolved model/effort, effective prompt/schema, destinations and run controls; the result is atomically checkpointed and tracks per-occurrence, per-document and per-request outcomes, budget use/reservations and throttle transitions. Planning makes no OCR inference requests and publishes no converted Markdown. Revalidate bound inputs before execution/resume; fail explicitly on drift or incompatible plan version. Invalid, unknown, budget-blocked and rate-limited work must be distinguishable from completion.
- Schedule across the batch in both modes: one active conversion job is one Paddle image job or one LLM document request. Start at three by default; the runner can start lower. On HTTP 429 drain already admitted work, reduce new admission from three to two to one, and stop new work on another 429 at one. Responses from a previous concurrency level do not skip a level. Do not retry the rate-limited or outcome-unknown request automatically. An explicit runner-authorized retry is a new admitted request and consumes new request/token budget. Authentication or hard quota failures stop new admission without being treated as soft throttling.
- LLM request and total token budgets are independent optional run-wide caps; each defaults to unlimited. Token accounting includes input (including vision), output and any reasoning tokens, without double-counting provider subtotals. With a hard cap, atomically record request admission and reserve a conservative upper bound on token usage, including the provider-enforced output maximum, before sending. Reconcile actual reported usage; hold budget if status/usage is unknown. If a trustworthy upper bound cannot be established or cannot fit, do not submit; do not use the ten-document pilot's per-image estimate as a hard guarantee. Resume cannot reset counts or drop unresolved reservations. Keep maximum per-request context/output limits even for unlimited runs.
- Persist accepted image patches before publication and reuse them only when their exact inputs and effective conversion configuration still match. Do not auto-replay a submitted request with unknown outcome or re-submit already successful work. Do not expose secrets in plans, results, diagnostics or logs. Publish a complete document atomically to its mapped destination only when each required occurrence has an accepted result; held/failed documents are reported and do not replace a prior valid output. Leave original sources and image files untouched.

### OCR content and consumers

- Replace a successful image reference at its original location with a versioned OCR block: explicit start, bound metadata, original image link, converted Markdown content and explicit end. Retain provenance and ensure local original-image links resolve from the output location. Outside replacement ranges keep authored bytes unchanged. Protect the format from model-generated delimiters and keep uncertain or failed occurrences attributable; never publish an incomplete document as complete.
- Default LLM prompt: tables are **Markdown tables only**, not Mermaid or HTML. Represent merged/multilevel headers by an explicit Markdown expansion that preserves associations; retain field names, units, and values, and clearly mark unreadable content. Convert flowcharts and mind maps to Mermaid, preserving visible relations, arrows and labels. For charts and explanatory slides, output visible transcription, a separate summary and a separate translation (default target Simplified Chinese; editable through the effective prompt). For other images output a text summary. For mixed images use each appropriate format. Do not invent missing values, labels, arrows or facts. Structural validation is not a guarantee of factual fidelity.
- Update active consumers for the **new** contracts: `dogpdkb` invokes the public `mdocr` CLI and reads documented artifacts only; `mdcompare` recognizes new blocks and evidence without treating generated OCR as authored text. Do not import tool internals across package boundaries. Keep production on the existing release until the new CLI and both live consumers pass joint acceptance. Do not support old inputs, plans, reports, caches or OCR block formats as a migration requirement.

## Testing Decisions

- Primary seam: invoke the installed public `mdocr` CLI as a subprocess, using fixture Markdown, local images and local fake provider behavior. Assert durable artifacts, output bytes, public statuses, provider request counts and order constraints; do not assert internal thread, SDK or storage implementation details. Reuse the existing subprocess/local Paddle service tests and CLI help/install checks as prior art.
- Cover each input shape, recursion, ordering, single/list/directory output mapping, one repeated image, syntax in literal code, path collisions, invalid or drifted input, plan-only no inference/no publication, independent Paddle operation, provider/vision/effort rejection, and default and overridden prompt/model settings. Verify finished documents preserve authored bytes outside replacements and the saved source link resolves. Inject missing/duplicate/misordered image IDs, truncation, malformed response, reserved delimiters and explicit hold; never publish a false completion.
- Exercise real simultaneous fake requests in both modes, controlled 429 at three, two and one, permanent 401/403, successful in-flight settlement, no replay, explicit resume, no capped-budget oversubscription, unknown usage and restart with outstanding reservations. Budget stop must prevent actual submissions, not just change the final report.
- Exercise Markdown table rows/column width and actual Mermaid parsing, and review representative original images against converted output for visible cells/units, branch direction, diagram hierarchy and translation boundaries. Treat renderer parse success as structural evidence only.
- Joint CLI-level acceptance: `dogpdkb` reads new plan/output and respects withheld documents; `mdcompare` identifies the new OCR block as generated and retains original-resource evidence. Installation tests prove Paddle works without Node/Pi and LLM fails clearly if unavailable. Separately, run a narrow synthetic-image OAuth smoke on `openai` with default medium and on `openai-codex` with Pi Device Code login. Do not use real corpus documents for these live gates.

## Out of Scope

- Historical Markdown/OCR files, plans, reports and their compatibility/migration; imported old caches; historical corpus repair.
- Non-OpenAI OAuth providers, direct inference via API keys, custom auth, extension-defined providers and arbitrary model fallback for the first release.
- Renegotiating the formal `mdcompare` scoring CLI. Its current released routing command remains offline; the scoring experiment is prior art for SDK invocation and strict Schema only.
- Production install/cutover, full knowledge-base download or rebuild, mass model calls, and removal of Paddle or original images.

## Further Notes

- The Terra pilot via `openai` with low effort accepted strict responses for ten documents and 23 images; 14 Mermaid blocks parsed. An oversized vision estimate was insufficient for one tall image. These observations support the design but are not acceptance evidence for medium effort, Device Code, new cache/plan behavior, or factual accuracy at scale.
- The test-only Paddle concurrency change and existing local fake HTTP acceptance are useful prior art, not the new public contract. Avoid bringing unrelated local-sync changes into this rewrite.
- A smaller valid diagram or a partial literal transcription may be appropriate only when omissions are explicit. The original image remains the evidence source.
