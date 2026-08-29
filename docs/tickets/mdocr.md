# feat: add the `mdocr` Markdown image OCR CLI

Status: ready for implementation  
Source specification: [`../spec/mdocr.md`](../spec/mdocr.md)

## Outcome

Provide an installable `mdocr` command that prepares Markdown images as local JPEG assets and can replace eligible image embeds with PaddleOCR-produced Markdown while preserving traceability to the prepared image.

## Scope

### Package and CLI

- Add Python package metadata and a console entry point named `mdocr`.
- Declare Requests and Pillow as runtime dependencies.
- Add `prepare` and `ocr` subcommands.
- Support `-f/--file FILE [FILE ...]`, `-o/--output-dir DIR`, `--download`, and `--force`.
- Make no-argument invocation display top-level help and exit successfully.
- Read PaddleOCR credentials only from `PADDLEOCR_TOKEN`.

### Shared Markdown and image preparation

- Parse inline Markdown image syntax only.
- Resolve relative image paths from each input Markdown file.
- Reject duplicate input basenames before processing.
- Write flat output Markdown files under the required output directory.
- Store normalized images under `assets/<markdown-stem>/` with deterministic collision-safe `.jpg` names.
- Decode and normalize images with Pillow, including EXIF orientation, first-frame selection, and RGB conversion.
- Skip remote URLs by default with a warning.
- Download and prepare `http://` and `https://` images only when `--download` is present.
- Preserve unsupported image forms unchanged with a warning.
- Write output Markdown atomically.

### `prepare`

- Copy each input Markdown file to the output directory.
- Replace every successfully prepared image URL with its relative generated JPEG path.
- Preserve alt text and optional image titles.
- Leave skipped or failed image embeds unchanged.

### `ocr`

- Accept raw Markdown without requiring a prior `prepare` run.
- Reuse the PaddleOCR job submission, polling, result download, and extraction behavior from `scripts/paddleocr_convert.py`.
- Use the existing PaddleOCR job API and `PaddleOCR-VL-1.6` model.
- Replace PaddleOCR-emitted nested images with textual placeholders.
- Replace every successfully OCRed source image at its original location with:

  ```markdown
  <!-- mdocr:ocr-begin source="assets/<stem>/<image>.jpg" -->

  <OCR Markdown>

  > Original image: [assets/<stem>/<image>.jpg](assets/<stem>/<image>.jpg)

  <!-- mdocr:ocr-end -->
  ```

- Preserve existing mdocr OCR blocks on ordinary runs.
- With `--force`, permit output replacement and freshly OCR existing blocks from their recorded local source JPEG.
- Do not add persistent result caching or resumable job state.

### Failure handling

- Continue after an individual image fails.
- Preserve each failed image's original embed.
- Emit diagnostics containing the input file and image reference.
- Write all successful transformations as partial output.
- Exit nonzero if any non-skipped image or file fails.
- Treat intentionally skipped remote and unsupported image forms as warnings, not failures.
- Validate arguments, files, output collisions, token availability, and duplicate basenames before submitting any OCR jobs.

## Tests

- CLI help and no-argument behavior.
- Multiple input files and duplicate-basename rejection.
- Output collision behavior with and without `--force`.
- PNG, WebP, GIF, and JPEG normalization into valid JPEG assets.
- Local-link rewriting and alt/title preservation.
- Remote skip behavior and opt-in mocked downloads.
- Raw-Markdown OCR flow with mocked PaddleOCR HTTP requests.
- Exact OCR block structure and local source attribution.
- Existing block preservation and `--force` re-OCR behavior.
- Partial output and nonzero status after one image fails.
- Atomic output replacement.
- Automated tests must not use the network or a real PaddleOCR token.

## Definition of done

- `pip install -e .` exposes `mdocr`.
- Every acceptance criterion in [`docs/spec/mdocr.md`](../spec/mdocr.md) passes.
- Focused tests pass without network access.
- LSP and repository diagnostics report no new blocking errors.
- Changes remain limited to the CLI package, package metadata, tests, and required documentation updates.

## Explicit non-goals

- In-place source modification
- Recursive input discovery
- HTML or reference-style image support
- OCR-provider abstraction
- Persistent OCR cache or job database
- Parallel scheduling
- Custom image codecs
