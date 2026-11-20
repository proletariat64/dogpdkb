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

## Requests, caching and resume

A provider request includes exactly one image and the configured OCR instructions.
It does not include authored Markdown, paths, occurrence IDs or peer images. The
strict JSON response contains `kind`, `markdown` and `uncertainty`. The runner builds
the v2 wrapper and document evidence from the original plan.

In LLM mode up to three Markdown pipelines run by default, processing selected
images sequentially within each document. A 429 drains active requests before
admission continues at the lower concurrency, finishing already-started pipelines
before admitting more documents. A 429 at one stops further admission. Paddle
keeps its per-image concurrency. The global request budget counts image admissions
across resumes, not cache hits. If configured, the image cache persists valid
results even when a sibling image is unknown, keyed by original image SHA-256
and mode/model/effort/prompt/schema; the prior document-context cache is not
reused. Markdown is assembled locally after OCR work settles, and an unresolved
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

Install the local Mermaid parser once with `npm install --prefix "$HOME/.local/share/mdocr/node" --ignore-scripts mermaid@12.3.0 jsdom@26.1.0`. Set `MDOCR_NODE_DEPENDENCIES` to that prefix. The parser loads locally, can run offline, and receives no OAuth credential. The bridge still refuses missing/unclosed Mermaid fences or Mermaid output when
the parser is unavailable, including when an individual diagram parser cannot
load. If a closed Mermaid diagram has a recognized syntax error, the otherwise
valid OCR text is preserved unchanged. The `mdocr-result-2`
document resource entry records `warnings: ["response_mermaid_syntax_invalid"]`
alongside that image’s ID and resource ID, including on cache reuse. The warning
does not change completion status, the original-byte witness, or graph
publication requirements.

The bridge exposes no live vision input token ceiling from the installed provider descriptors. A hard total-token limit must fail closed unless the runner has a justified upper bound. Request-count limits are independent and unaffected. Do not interpret a successful local runtime preflight as real inference acceptance for either provider.
