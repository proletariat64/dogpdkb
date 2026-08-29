
repo root

## .gitignore 
inbox/  raw knowledge materials before standardized 
graphify_ready/ standardized ones
graphify_output/ compiled llm graphic wiki
skylark_books/ skylark URLs and its sync records
## gitkeep
scripts/

## mdocr — Markdown image prepare/OCR CLI

`mdocr` prepares images referenced by Markdown and can replace them with OCR
markdown from the PaddleOCR API. Install once, then run from anywhere:

```bash
pip install -e .            # exposes the `mdocr` command
```

Usage:

```bash
mdocr -h                    # top-level help
mdocr ocr -h                # per-command help

mdocr prepare -f doc.md -o out/          # normalize local images to JPEG assets
mdocr ocr -f doc.md -o out/              # replace images with OCR markdown
mdocr ocr -f a.md b.md -o out/ --download  # multiple files + fetch remote images
mdocr prepare -f doc.md -o out/ --force  # overwrite existing outputs
```

Notes:

- Input files are never modified; outputs go to `-o DIR` (`<name>.md` plus
  `assets/<name>/` JPEG copies, links rewritten).
- Remote images are skipped with a warning unless `--download` is given.
- `ocr` reads the `PADDLEOCR_TOKEN` environment variable for the PaddleOCR API.
