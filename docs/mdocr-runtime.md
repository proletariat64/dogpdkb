# Pi-backed OCR runtime prerequisites and limits

Paddle does not install or load Node. For LLM mode, install Pi coding agent (tested model-runtime preflight: 1.5.2) and Node meeting that Pi release's engine requirement (22.19.0 or newer for Pi 1.5.2). The bridge locates the installed SDK from the real path of `pi` or an explicit Pi package root via `MDOCR_PI_SDK_DIR`. OpenAI and OpenAI Codex Responses routes only; log in from Pi and use the exact provider prefix. Pi owns its OAuth store and refresh. `PI_CODING_AGENT_DIR` applies to both credential and model configuration paths. API keys and alternate endpoints are not eligible.

## Vision image inputs

LLM planning downloads explicitly selected remote images without imposing
Paddle's filename-extension filter. On execution, LLM checks the original
bytes and detects the actual format. PNG, JPEG, WebP and GIF are sent in their
native encodings. Other still images decodable by the installed Pillow (e.g.
TIFF, BMP, ICO and AVIF with a decoder) are sent as PNG; for multi-frame images
that require conversion, the first frame is used. Corrupt or undecodable input
holds the document instead of publishing an incomplete OCR result.

For SVG, install Google Chrome or Chromium on `PATH`. `mdocr` renders an SVG
as an isolated image to a temporary PNG, including inline HTML labels in
Mermaid SVGs. The renderer does not load external image resources or execute
SVG scripts. A separate referenced image must be embedded first. SVG plans
and OCR evidence retain the **original SVG bytes, SHA-256 and link**, not the
screenshot. Raster-only LLM runs and all Paddle runs do not start Chrome.

Chromium SVG profiles are per-image. If profile files race with teardown,
mdocr retries the local cleanup without re-rendering or sending the image.
Persistent `ENOTEMPTY` is a known pre-send image failure, eligible for the
bounded batch fixer; it does not abort other images or document assembly.
Invalid or unbound batch results are not OCR retry signals. Recovering accepted
patches requires their original plan and verified request journal, not a resend.

## Requests, caching and resume

A provider request includes exactly one image and the configured OCR instructions.
Only a Mermaid repair request also includes the previous OCR patch and local
parser diagnostics. Requests do not include authored Markdown, paths, occurrence
IDs or peer images. The
strict JSON response contains `kind`, `markdown` and `uncertainty`. The runner builds
the v2 wrapper and document evidence from the original plan.

`mdocr plan --concurrency` accepts 1–100 workers (default 3). In LLM mode this
caps concurrent Markdown pipelines; each document processes selected images
sequentially. A 429 drains active requests before
admission continues at the lower concurrency, finishing already-started pipelines
before admitting more documents. A 429 at one stops further admission. Paddle
keeps its per-image concurrency. The optional request budget counts each initial, batch fix or repair admission across
resumes, not cache hits. Initial OCR requests are scheduled before optional
repairs. If configured, the image cache persists valid
results even when a sibling image is unknown, keyed by original image SHA-256
and mode/model/effort/prompt/schema and repair policy; the prior
document-context cache is not reused. With repair enabled, results that still
have a Mermaid syntax warning are not cached. Markdown is assembled locally after OCR work settles, and an unresolved
image prevents publication of its document, not of other completed documents.
Resume with the same plan, journal and result; unknown image jobs are never
replayed on that plan. An unrelated new standalone plan has no knowledge of
unknown attempts from other journals. Legacy LLM plans are read-only historical
evidence; do not replan previously unknown OCR work.

The LLM journal records fixed reason codes and aggregate token usage when
available. `response_refusal` is a typed provider refusal, not inferred from text.
`response_output_limit`, `response_content_filter`, and `response_incomplete`
identify terminal incomplete responses. Other `response_*` codes distinguish JSON,
schema, table and Mermaid fence validation; `mermaid_parser_unavailable` indicates
local configuration. No refusal message, raw provider reason, or rejected output
is included in these diagnostics. Missing terminal evidence or invalid bridge
protocol after possible submission is `unknown_outcome`, not a retryable failure.
Older `invalid_response` journal entries do not record which check failed.

## Mermaid and inference limits

The release installer installs and verifies the local Mermaid parser in the
managed environment. For a standalone wheel or source checkout, install it with
`npm install --prefix "$HOME/.local/share/mdocr/node" --ignore-scripts mermaid@12.3.0 jsdom@26.1.0`
and set `MDOCR_NODE_DEPENDENCIES` to that prefix. An explicit value overrides the
managed dependencies. The parser runs locally without network or OAuth
credentials. The bridge refuses missing/unclosed Mermaid fences or Mermaid output when
the parser is unavailable, including when an individual diagram parser cannot
load. Only recognized syntax errors in closed Mermaid fences are repair candidates.
`MDOCR_MERMAID_REPAIR_ROUNDS` is read by `mdocr plan`: unset means at most 3 repair
requests per image, `0` disables repairs, and older plans without the field stay
at `0`. Each repair receives the original image, previous patch and parser block
number/message. Parser messages are not persisted. Repairs must pass the regular
response checks, and changes to already-valid diagrams, fence lines, non-Mermaid text, kind or uncertainty
are rejected.

The initial otherwise valid OCR patch stays available if repair fails, rounds are
exhausted, or the existing admission limits prevent repair. Its document resource
still carries `warnings: ["response_mermaid_syntax_invalid"]`. If a repair is
interrupted or its outcome is unknown, resume keeps the accepted OCR and does not
resend the repair. When the journal proves that the next repair was never
admitted, resume regenerates its diagnostics locally. An older runtime's request
identities that no longer match the current plan are rejected before any send. Repairs are recorded separately with `repair_round` and consume
existing request accounting; no spend cap is introduced by this feature. The warning
does not change completion status, the original-byte witness, or graph
publication requirements.

The bridge exposes no live vision input token ceiling from the installed provider descriptors. A hard total-token limit must fail closed unless the runner has a justified upper bound. Request-count limits are independent. Do not interpret a successful local runtime preflight as real inference acceptance for either provider.
