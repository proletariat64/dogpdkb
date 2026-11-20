"""Installed, offline command contracts for mdocr."""

ROOT = """Start here:
  mdocr plan --mode paddle --input doc.md --output enriched.md --plan plan.json
  mdocr run --plan plan.json --result result.json

Choose paddle for PaddleOCR (PADDLEOCR_TOKEN) or llm for multimodal conversion
through installed Pi and Node. Use Pi's /login; mdocr does not own credentials.
Use --help --full on the first command to inspect inputs, outputs and behavior.
Help/version perform no network access and create no files.
"""

ROOT_FULL = """Full contract:
  OCR block: marked generated Markdown linked to its original image.
  Prepared image: validated JPEG submitted by the Paddle backend.
  Use mdocr plan --help --full for input shapes and controls, and
  mdocr run --help --full for execution and recovery. mdocr prepare only
  prepares local image assets; it does not perform OCR.
"""

DOCTOR = """Examples:
  mdocr doctor --mode prepare
  mdocr doctor --mode paddle --json
  mdocr doctor --mode llm --model openai/gpt-6.1-terra

Default paddle mode checks Python dependencies and PADDLEOCR_TOKEN presence.
Prepare mode requires no credentials; LLM mode checks Pi, model/effort, stored
OAuth, and parses a local Mermaid sample. Expired OAuth is a warning.
No network, inference, refresh, secrets or report files. This does not verify
acceptance by the provider. --json emits mdocr-doctor-1 on stdout.
Exit 0: no failing check; 2: environment check or arguments failed.
Use mdocr doctor --help --full for the complete contract.
"""

DOCTOR_FULL = """Local readiness details:
  A usable stored OAuth entry is not proof of account access or quota. Doctor
  never refreshes it. Run `pi` and use /login if no credential is stored.
  For LLM mode set MDOCR_PI_SDK_DIR if Pi cannot be located and set
  MDOCR_NODE_DEPENDENCIES to the Mermaid/jsdom npm prefix.
  For Paddle mode a present token is not proof of remote acceptance.
"""

PLAN = """Examples:
  mdocr plan --mode paddle --input doc.md --output enriched.md --plan plan.json
  mdocr plan --mode llm --list paths.json --output-dir enriched --plan plan.json
  mdocr plan --mode llm --directory raw --output-dir enriched --plan plan.json

Paths in a JSON list are relative to that list. Directory mode is recursive.
A plan writes new JSON and optionally downloads explicitly selected remote
images with --download. It does not call an OCR provider or publish Markdown.
Run the plan with mdocr run --plan plan.json --result result.json.
Use mdocr plan --help --full for the complete contract.
"""

PLAN_FULL = """Plan controls:
  mdocr-plan-2 binds source bytes, image occurrences and witnesses, settings,
  and destinations. --selection is a JSON object mapping absolute input
  paths to lists of selected image targets; an empty list selects none.
  With --download, --download-list restricts remote downloads to a JSON array
  of absolute input paths. Other documents may still OCR local images.
  --mode llm defaults to openai/gpt-6.1-terra with medium effort. Use
  --model provider/model, --effort, and --prompt-file to override. The response
  schema stays code-owned. --token-budget and --request-budget are run-wide
  nonnegative ceilings; omit them for unlimited. A hard token budget refuses
  admission if a trustworthy upper bound is unavailable. --concurrency accepts
  1 through 10 (default 3): active Markdown pipelines for LLM, active image
  jobs for Paddle. The request budget counts image admissions, not documents.
  --max-seconds is an admission deadline (default 900).
"""

RUN = """Example:
  mdocr run --plan plan.json --result result.json

Tool credentials: set PADDLEOCR_TOKEN for a new Paddle job, or log into the
chosen OpenAI OAuth route in Pi for LLM. Existing local successful cache results
need no credentials. Completed documents are atomically published; failed or
unknown work is written to the result and never passed off as completed.

Exit codes: 0 completed; 2 incomplete or unsupported arguments; 1 invalid plan,
invalid result binding, or other preflight/write failure.
Use mdocr run --help --full for the complete contract.
"""

RUN_FULL = """Execution and recovery:
  Reuse the same plan and result paths when resuming. The private plan-side
  journal is authoritative for prior submissions, even if the result mirror was
  not written. Do not delete or move it to restart an incomplete plan.
  Legacy document-context LLM plans cannot be executed. In LLM mode images
  run sequentially within each Markdown pipeline; only an image is sent.
  HTTP 429 reduces later admission 3 → 2 → 1, then stops. Known failed requests
  require --retry-failed; unknown outcomes are never automatically replayed.
  Total token accounting includes vision and provider-reported output. Each
  mdocr-result-2 document row records output digest and mdocr-ocr-2 evidence
  only if complete. Local original image paths in OCR blocks stay traceable.
"""

FILE_CONTRACT = """Inputs and paths:
  Input is one or more Markdown files, not a JSON manifest. CLI paths are
  relative to cwd; local image references resolve from each input's directory.
  Duplicate input basenames are rejected before processing. No config is needed.

Outputs and effects:
  Writes DIR/<input-basename> and Prepared image JPEGs under
  DIR/assets/<input-stem>/. Markdown publication is atomic per file, not per run.
  Use a separate output directory to preserve inputs: --force can replace a
  same-basename input if the output directory points at that input's directory.
  Existing output Markdown is refused by default. --force permits replacement
  of regular files, not directories or symlinks. Partial files remain usable.
  stdout: no result payload. stderr: per-file/image warnings and errors.
  No batch JSON report is written; inspect output Markdown and stderr.

Exit codes:
  0: completed, including intentional skips with warnings.
  1: one or more processing/write failures; successful transformations remain.
  2: usage/preflight failure, including missing inputs or output collisions.

Next steps:
  Read stderr and inspect output Markdown before rerunning. Correct paths,
  unavailable formats, or individual image failures first. Another invocation
  refuses existing outputs unless --force is set; choose a fresh directory to
  keep prior results. No resume command or automatic retry policy is provided.
"""

PREPARE = (
    """Example:
  mdocr prepare -f doc.md -o out
  mdocr prepare -f doc.md -o out --download --format bmp,tga

Preparation never calls PaddleOCR and needs no PADDLEOCR_TOKEN. Supported inline
Markdown image links become relative links to local Prepared image JPEGs.
Remote downloads are disabled unless --download is present. This command never
submits OCR jobs for existing OCR blocks. --force only permits overwriting output
Markdown for this command.
"""
    + FILE_CONTRACT
    + """
Full local contract: mdocr prepare --help --full
"""
)

FILE_FULL = """Full contract:
  Supported image syntax is inline Markdown: ![alt](path-or-url "optional title").
  Local paths and HTTP(S) targets are recognized; reference images, HTML img,
  data URIs, and other schemes remain with warnings. Input files are decoded
  as UTF-8 with replacement for invalid bytes.
  Defaults: .png .webp .gif .jpg .jpeg .tif .tiff (case-insensitive).
  --format adds extensions; repeated comma-separated values and leading dots
  are accepted. Validation uses the installed Pillow build's readable formats.
  Missing/disabled extensions remain unchanged with warnings. Remote eligibility
  uses the URL path without query/fragment and is checked before downloading.
  Enabled local images are always prepared. Remote images are intentional skips
  without --download. Unsupported/skipped images do not cause nonzero exits.
  JPEG inputs must decode as JPEG and are copied byte-for-byte with .jpg suffix.
  Other formats use the first frame, EXIF orientation, RGB conversion and JPEG
  quality 90. Prepared image filenames are deterministic for the source bytes.
  Downloads use a 30-second request timeout. A failed image remains unchanged;
  processing continues with later images and files. Prepared images already
  written can remain after a later file failure; output is not transactional.
  No public JSON input/output schema applies to these file-oriented commands.
"""

PREPARE_FULL = (
    FILE_FULL
    + """
  prepare rewrites eligible image links, preserving alt text and optional title.
  It neither submits OCR jobs nor replaces existing OCR blocks with new text.
  --force enables output-file replacement only; no model processing is added.
"""
)
