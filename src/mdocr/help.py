"""Installed, offline command contracts for mdocr."""

ROOT = """Start here:
  mdocr prepare -f doc.md -o prepared
  mdocr ocr --help
  mdocr batch --help

Choose prepare for local image preparation without model calls, ocr for direct
Markdown-to-OCR conversion, or batch for JSON manifests and structured outcomes.
Downloads are opt-in. New OCR work needs PADDLEOCR_TOKEN; do not put credentials
in command arguments or manifests.

Local documentation:
  mdocr COMMAND --help        first invocation, effects, exits, and next steps
  mdocr COMMAND --help --full complete schema and boundary behavior, offline
  mdocr --version             installed distribution version

Help/version require no configuration or token and create no operational files.
"""

ROOT_FULL = """Full contract:
  Prepared image: validated local JPEG retained for traceable OCR and re-OCR.
  OCR block: marked Markdown containing OCR text and its Prepared image link.
  prepare and ocr accept Markdown files, not JSON manifests. batch accepts a
  versioned JSON manifest. Paths passed as CLI arguments resolve from cwd;
  references inside Markdown resolve from that Markdown file's directory.
  There is no scheduler, corpus policy, or automatic retry/resume layer here.
  Commands retain usable output after isolated failures; inspect diagnostics
  or the batch report before selecting another invocation.
  Use mdocr prepare --help --full, mdocr ocr --help --full, or
  mdocr batch --help --full for the complete operation-specific contract.
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

OCR = (
    """Example:
  mdocr ocr -f doc.md -o out
  mdocr ocr -f doc.md -o out --download

Authentication: set PADDLEOCR_TOKEN in the process environment before invoking
ocr, even when the document has no new images. Do not put the token in argv.
The command accepts raw Markdown directly; prepare is not a required first step.
Eligible images become Prepared image JPEGs, then model calls produce a marked
OCR block containing Markdown and its original-image link. Nested model images
are replaced with textual placeholders. Remote downloads require --download.
Existing OCR blocks are preserved unless --force requests fresh re-OCR from
their local Prepared image. For re-OCR, existing block sources must be canonical
relative assets/...jpg paths already present under the destination output
directory, not merely beside the input Markdown. Copy that prepared assets tree
when moving an existing OCR document to a new destination before using --force.
There is no persistent OCR-result cache here;
batch has a separate successful-result cache. New work can incur model charges.
"""
    + FILE_CONTRACT
    + """
After partial processing, preserve successful OCR blocks and repair failed image
sources. A fresh output directory avoids overwrites for raw inputs; existing
OCR blocks need their prepared assets copied into that destination for re-OCR.
--force may repeat successful model work; do not use it as a blanket retry.
Full local contract: mdocr ocr --help --full
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

OCR_FULL = (
    FILE_FULL
    + """
  Paddle endpoint: https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
  Model: PaddleOCR-VL-1.6. The ocr command has no endpoint CLI override.
  HTTP requests have a 60-second timeout and bounded transport retries; polling
  uses 5-second intervals and a 900-second job deadline. These are not a total
  multi-file run budget. batch exposes its own endpoint and wall-time options.
  Successful replacement is an OCR block enclosed by mdocr:ocr-begin and
  mdocr:ocr-end HTML comments. The begin marker's source points at the Prepared
  image; the block ends with an Original image Markdown link to the same JPEG.
  --force permits both existing output replacement and new OCR of existing
  blocks using their source JPEG. Unlike raw Markdown references, an existing
  OCR block's source must be a canonical relative assets/...jpg path resolved
  under --output-dir, not the input Markdown directory. Re-OCR to a new output
  directory requires copying its prepared assets tree there first. Absolute,
  escaping, or noncanonical sources are rejected. A failed replacement preserves
  the old block and yields a processing error; it does not fetch a missing JPEG.
  Without --force, existing OCR block contents and source links are protected.
  Transport, polling, result parsing and individual image errors go to stderr;
  later images/files continue, and retained partial output exits 1.
"""
)

BATCH = """Example:
  mdocr batch --manifest input.json --output-dir out --report report.json

Minimal input.json (place doc.md next to it):
  {"schema_version":"mdocr-batch-input-1.0","documents":[
    {"document_id":"doc-1","path":"doc.md","resources":[]}
  ]}
Omit resources to select all supported inline images; [] selects no new OCR.
Selection strings match parsed image targets exactly and are not normalized:
local a%20b.png#preview matches a b.png; HTTP(S) targets keep percent-encoding.
Document IDs must be unique, nonempty strings. Document paths resolve relative
to the manifest; image references resolve from each source Markdown directory.
All CLI paths are relative to cwd. This command needs no configuration file.

Authentication and processing:
  PADDLEOCR_TOKEN is required only for a new OCR call, not successful cache reuse.
  Remote downloads require --download and can occur while checking cache reuse,
  even for unselected images. A selected uncached resource can trigger a model
  call. Only successful OCR is cached, and matching bytes/configuration are
  verified before reuse; a URL alone never authorizes reuse.
  Shared auth/quota failure stops later selected uncached work; isolated failures
  do not stop other documents. The default batch wall-time budget is 900 seconds.

Outputs and effects:
  Writes enriched Markdown, Prepared image JPEGs and successful cache artifacts
  under output/cache directories; atomically replaces output Markdown and report.
  Input manifest, Markdown and referenced images are protected from report/output
  overlaps. Use dedicated output and cache directories. Use reported absolute
  output paths; do not guess filenames or inspect the cache's internal layout.
  stdout: no result payload. stderr: fatal invocation/manifest/storage errors.
  --report writes mdocr-batch-1.0 JSON with status, documents, resource counts,
  shared causes, per-resource outcomes and successful prepared-jpeg-ocr-v1 evidence.
  Failed/unattempted/unselected images remain in Markdown; local references are
  made absolute so the returned Markdown can be relocated.

Exit codes:
  0: every document completed; selected resources succeeded or were reused.
  2: partial structured report with failed/unattempted resources or documents.
  1: invalid manifest/budget or fatal output/report failure; inspect stderr.
  CLI usage errors also exit 2, before processing, with a diagnostic on stderr;
  distinguish these from partial results by the invocation and actual report.

Next steps:
  Read report.status, documents, resources and causes. Retain usable outputs and
  repair reported failures before another batch invocation. There is no resume
  command: a repeat reevaluates the manifest, replaces outputs/report, reuses
  validated successful cache entries, and may download or submit uncached work.
  Preserve the cache to avoid repeating successful model calls; removing it
  forces fresh work. Do not blindly retry auth/quota or deadline failures.
Full local contract: mdocr batch --help --full
"""

BATCH_FULL = """Full contract:
Input JSON schema:
  Object with schema_version exactly "mdocr-batch-input-1.0" and documents array.
  The array may be empty. Each document is an object with document_id (required,
  unique nonempty string), path (required nonempty string; absolute or relative
  to the manifest), and resources (optional array of nonempty image targets).
  Omitted or null resources selects every supported inline Markdown image;
  [] selects none. Selection entries are compared verbatim against parsed
  Markdown targets; the selection strings themselves are not normalized. Parsed
  local targets are percent-decoded and have fragments removed: the Markdown
  target a%20b.png#preview needs selection "a b.png", not "a%20b.png#preview".
  Parsed HTTP(S) targets lose fragments but retain percent-encoding, so an image
  https://example.invalid/a%20b.png#preview needs that URL without #preview,
  preserving %20. A selected string absent from parsed targets is reported as
  RESOURCE_NOT_FOUND and does not select a different target implicitly.
  Additional object fields are ignored; document_id/path cannot be null.
  Unsupported/reference/HTML syntax is retained with warnings. Supported image
  defaults are .png .webp .gif .jpg .jpeg .tif .tiff; batch has no --format option.
  Repeated occurrences of an image target share one resource record.

Report JSON schema (mdocr-batch-1.0):
  schema_version: "mdocr-batch-1.0".
  status: "completed" or "partial".
  documents: array in manifest order. Each object contains:
    document_id: original string identifier.
    path: absolute source Markdown path.
    output: absolute enriched Markdown path, or null if document reading failed.
    status: "completed", "partial", or "failed".
    warnings: string array for unsupported/skipped syntax.
    resources: array of resource records described below.
    errors: string array after document transformation; absent on read failure.
    error: "DOCUMENT_READ_FAILED" on document read/UTF-8 failure; otherwise absent.
    ocr: successful evidence object described below, including an empty images
      array when no new or reused successful evidence was produced.
  counts: object mapping resource status strings to integer counts of per-document
    resource records. Repeated references to one target within a document share
    one record, while the same target in another document has its own record.
    Absent status keys have zero records. These are not reference-occurrence or
    document counts.
  causes: array, empty or containing the shared stop reason for this batch.

Resource record schema:
  resource_id: parsed Markdown target string without a URL fragment; local paths
    are percent-decoded, while HTTP(S) targets preserve percent-encoding.
  status: "success" (new OCR), "reused" (validated cache), "failed",
    "unattempted", or "unselected" (no selected new call and no cache hit).
  reason: optional code: IMAGE_PROCESSING_FAILED, RESOURCE_NOT_FOUND,
    BATCH_DEADLINE_EXCEEDED, OCR_AUTH_UNAVAILABLE, OCR_QUOTA_EXHAUSTED,
    or another shared OCR cause code.
  source_path, prepared_image, artifact: absolute paths present for successful
    or reused results. They point to retained original bytes, Prepared image
    JPEG, and the complete marked OCR block respectively. OCR text itself is
    not included as a resource field in the published report.

Successful evidence schema:
  ocr.contract: "prepared-jpeg-ocr-v1".
  ocr.preprocessing: object with name "mdocr" and installed version string.
  ocr.configuration: engine "paddleocr", model "PaddleOCR-VL-1.6", jpeg_quality 90.
  ocr.images: successful resource records with resource_id, status "success"
    (also for reuse), source_path, prepared_image and artifact. Enriched OCR
    blocks use absolute Prepared image paths; existing blocks are preserved
    with local source references relocated to absolute paths.

Boundary behavior:
  --cache-dir defaults to OUTPUT_DIR/.cache; it can persist across invocations.
  Unselected resources can still be prepared/downloaded and successful cache
  results reused. Selection restricts new model calls, not all disk/network work.
  A selected remote image without --download is a failed resource with reason
  IMAGE_PROCESSING_FAILED; unlike prepare/ocr, batch records this as partial.
  Cache validation compares retained original and prepared bytes plus processing
  configuration; unsuccessful results are never cached. No cache-management or
  purge command is provided. Caller owns safe cache retention.
  --max-seconds accepts finite nonnegative seconds (0 stops selected work before
  preparation); NaN, infinity and negative values are rejected. The budget is
  checked between operations and before preparation/OCR, not as a hard kill of
  an in-flight request. HTTP timeout is 60 seconds, polling interval 5 seconds,
  and each submitted job has its own 900-second deadline and bounded retries.
  --endpoint selects a compatible jobs URL; default:
    https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
  The model remains PaddleOCR-VL-1.6. Remote image download timeout is 30 seconds.
  Auth/quota failure marks the triggering image failed and later selected
  uncached images unattempted. Valid cache reuse can still complete later work.
  A missing/unreadable document is recorded as failed and does not stop others.
  Partial is determined by failed/unattempted resources, document failures, or
  transformation errors. Warnings and unselected resources alone do not cause it.
  A shared storage/write failure can exit 1 before a fresh report is published;
  an older report may remain. Atomic publication is per Markdown/report file,
  not transactional for the whole batch. Inspect stderr and report freshness.
"""
