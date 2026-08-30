# `mdocr` CLI specification

Status: approved design, awaiting implementation.

## Purpose

`mdocr` prepares images referenced by Markdown and can replace those images with Markdown returned by the PaddleOCR API. It preserves an offline JPEG copy of every successfully processed image and records that copy inside each OCR result block.

## Installation and entry point

The repository provides a Python package with a console-script entry point named `mdocr`. An editable installation must make the command available:

```bash
pip install -e .
mdocr --help
```

Runtime dependencies are Requests and Pillow. PaddleOCR authentication comes from the `PADDLEOCR_TOKEN` environment variable; credentials are not accepted as command-line arguments.

Running `mdocr` without arguments displays the same top-level help as `mdocr -h` / `mdocr --help` and exits successfully.

## Commands

```text
mdocr prepare -f FILE [FILE ...] --output-dir DIR [--download] [--format EXT[,EXT...]]... [--force]
mdocr ocr     -f FILE [FILE ...] --output-dir DIR [--download] [--format EXT[,EXT...]]... [--force]
```

`-f` and `--file` are equivalent. `-o` may be provided as the short form of `--output-dir`. `--format` is long-only because `-f` is already the file option.

Both commands:

1. accept one or more Markdown files;
2. require an output directory;
3. preserve the input files;
4. write one output Markdown file per input; and
5. process files and images on a best-effort basis.

## Input and output layout

Output Markdown is flat under the output directory:

```text
<output-dir>/<input-basename>.md
```

Prepared images are scoped to their Markdown file:

```text
<output-dir>/assets/<input-stem>/<deterministic-name>.jpg
```

Generated Markdown links use relative POSIX-style paths, for example `assets/guide/diagram-a1b2c3d4.jpg`.

Before processing begins, `mdocr` rejects an invocation containing input files with duplicate basenames because they would target the same output file.

An existing output Markdown file is not overwritten unless `--force` is present. Writes are atomic so an interrupted write cannot leave a truncated Markdown file.

## Supported image syntax

Version 1 supports inline Markdown images:

```markdown
![alternative text](path-or-url)
![alternative text](path-or-url "optional title")
```

Local relative paths and `http://` or `https://` URLs are recognized. Relative local paths are resolved from the input Markdown file's directory.

Reference-style images, HTML `<img>` elements, data URIs, and other URI schemes remain unchanged and produce a warning.

## Supported image formats

By default, mdocr processes only these common filename extensions:

```text
.png .webp .gif .jpg .jpeg .tif .tiff
```

Extension matching is case-insensitive. For local images, mdocr checks the local filename. For remote images, it checks the URL path after ignoring its query string and fragment. An image with no extension or an extension that is not enabled remains unchanged and produces a warning. For remote images, this check occurs before any network request.

`--format` adds one or more non-default extensions for the current invocation; it does not replace the default set. The option may be repeated, and each value may contain a comma-separated list. Leading dots are optional, duplicate values are ignored, and values are normalized to lowercase. For example:

```bash
mdocr prepare -f doc.md -o out/ --format .sgi,.tga
mdocr ocr -f doc.md -o out/ --format sgi --format tga
```

An extension supplied through `--format` must be reported as readable by the installed Pillow build. Unsupported requested extensions are argument errors detected before image processing. The target Pillow-readable extension set is:

```text
.apng .avif .avifs .blp .bmp .bufr .bw .cur .dcx .dds .dib
.emf .eps .fit .fits .flc .fli .ftc .ftu .gbr .gif .grib
.h5 .hdf .icb .icns .ico .iim .im .j2c .j2k .jfif .jp2
.jpc .jpe .jpeg .jpf .jpg .jpx .mpeg .mpg .msp .pbm .pcd
.pcx .pfm .pgm .png .pnm .ppm .ps .psd .pxr .qoi .ras
.rgb .rgba .sgi .tga .tif .tiff .vda .vst .webp .wmf .xbm .xpm
```

Actual availability may vary by Pillow version, platform, installed decoder dependencies, and build options. mdocr must derive validation from Pillow at runtime rather than assume every extension in the target list is available. Formats not reported as readable by Pillow, including SVG, cannot be enabled through `--format`.

## Image preparation

Every successfully processed image is written as a real JPEG under the output asset directory. Preparation must use Pillow rather than implement image codecs.

For `.jpg` and `.jpeg` sources, mdocr validates that Pillow identifies the content as a readable JPEG, then copies the original bytes without pixel conversion, re-encoding, quality loss, or metadata changes. The generated asset still uses the canonical `.jpg` extension.

For every other enabled format, mdocr decodes the image with Pillow, applies EXIF orientation, uses the first frame of a multi-frame image, converts pixels to a JPEG-compatible RGB representation, and encodes the result as JPEG.

A filename extension controls eligibility, but does not replace content validation. A file whose bytes do not decode as its claimed enabled format is an image failure. Asset names must be deterministic and collision-safe; using a readable source stem plus a SHA-256 prefix of the original source bytes is acceptable.

### Remote images

Remote downloads are disabled by default.

- Without `--download`, remote image embeds remain unchanged, are not OCRed, and produce a warning. This is an intentional skip and does not make the command fail.
- With `--download`, remote images with an enabled extension are downloaded, validated, prepared as described above, stored under the output asset directory, and treated like local images.
- Remote images with a disabled or missing extension remain unchanged and produce a warning even when `--download` is present. mdocr must not request their URLs.

## `prepare`

`mdocr prepare` creates a prepared Markdown copy without calling PaddleOCR.

For each successfully prepared image, it replaces the original image URL with the generated relative JPEG path while preserving the image's alt text and optional title.

Example:

```markdown
![Architecture](../images/architecture.png)
```

becomes:

```markdown
![Architecture](assets/guide/architecture-a1b2c3d4.jpg)
```

Unsupported, skipped, or failed images remain unchanged.

## `ocr`

`mdocr ocr` accepts raw Markdown directly. Users do not have to run `prepare` first. The command applies the same format eligibility, download, validation, and preparation behavior internally before submitting each eligible image to PaddleOCR.

The PaddleOCR job submission, polling, result download, and result extraction behavior should be adapted from `scripts/paddleocr_convert.py`. Version 1 uses the existing PaddleOCR job API and the `PaddleOCR-VL-1.6` model. PaddleOCR-produced nested image embeds are converted to textual placeholders, as in the existing converter.

### Successful replacement

A successfully OCRed image embed is removed and replaced at the same position by this structure:

```markdown
<!-- mdocr:ocr-begin source="assets/guide/architecture-a1b2c3d4.jpg" -->

<OCR Markdown returned by PaddleOCR>

> Original image: [assets/guide/architecture-a1b2c3d4.jpg](assets/guide/architecture-a1b2c3d4.jpg)

<!-- mdocr:ocr-end -->
```

Requirements:

- Markers are HTML comments and therefore invisible in rendered Markdown.
- The `source` value and quoted link refer to the prepared local JPEG, not a remote URL or the source file outside the output tree.
- OCR Markdown is inserted without wrapping it in a code block.
- Each source image gets its own result block at its original location.

### Repeated runs

Without `--force`, existing `mdocr:ocr-begin` / `mdocr:ocr-end` blocks are preserved and their quoted source links are not interpreted as new images.

With `--force`, existing mdocr OCR blocks are eligible for replacement by a fresh OCR result using the local JPEG named by the block's `source` field. `--force` also permits replacement of existing output files.

Version 1 has no persistent OCR-result cache, resumable job database, or cache-management interface.

## Failure behavior

Processing continues after an individual image fails during reading, downloading, JPEG conversion, PaddleOCR submission, polling, or result parsing.

For each failed image:

- keep its original image embed unchanged;
- emit a diagnostic identifying the input file and image; and
- continue processing later images and files.

The command still writes partial output containing all successful transformations. It exits nonzero if any non-skipped image or file failed. Intentionally skipped remote images, disabled or missing image extensions, and unsupported image forms produce warnings but do not cause a nonzero exit.

Argument errors, duplicate output basenames, missing input files, output collisions without `--force`, and a missing `PADDLEOCR_TOKEN` for an OCR run fail before any PaddleOCR jobs are submitted.

## Acceptance criteria

1. `mdocr`, `mdocr -h`, and `mdocr --help` display top-level help; no-argument use succeeds.
2. By default, `prepare` processes PNG, WebP, GIF, JPEG, and TIFF inputs, writes valid local JPEG assets, and rewrites their inline Markdown links.
3. Valid `.jpg` and `.jpeg` sources are copied byte-for-byte into canonical `.jpg` assets without re-encoding.
4. Images with disabled or missing extensions remain unchanged with a warning; disabled remote URLs are not requested.
5. `--format` accepts repeated and comma-separated Pillow-readable extensions, adds them to the defaults, and rejects extensions unavailable in the installed Pillow build before processing.
6. Remote images remain untouched by default and eligible remote images are downloaded and rewritten only with `--download`.
7. `ocr` works directly from raw Markdown and does not require prior preparation.
8. Successful OCR replaces each image at its original position with the exact marked structure described above.
9. The quoted source link in each OCR block resolves to a generated local JPEG.
10. Existing OCR blocks are idempotently preserved unless `--force` is supplied.
11. One image failure leaves that embed unchanged, permits later work to complete, writes partial output, and causes a nonzero exit.
12. Multiple inputs are supported; duplicate basenames are rejected before processing.
13. Existing outputs are preserved unless `--force` is supplied, and output Markdown writes are atomic.
14. PaddleOCR HTTP behavior is covered by mocked tests; the automated test suite does not require network access or a real API token.

## Non-goals for version 1

- In-place modification of source Markdown
- Recursive directory discovery
- HTML or reference-style image parsing
- A generic OCR-provider abstraction
- Persistent OCR caching or resumable job state
- Parallel job scheduling
- Packaging a wheel for image conversion or implementing image codecs
