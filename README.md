
repo root

## data dir structure .gitignore

+- inbox/   raw knowledge materials before standardized
+- wiki/ llm wiki
 +- graphify_flatten_ready/  pre-processed knowledge markdowns
 +- graphify_output/  compiled llm graphic wiki
+- skylark_books/ skylark URLs and its sync records

## src

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

### PaddleOCR result download — bcebos CDN workaround

`mdocr ocr` downloads each OCR result JSONL from PaddleOCR's BOS bucket
(`paddleocr-store-N.bj.bcebos.com`). The job's result URL randomly picks a
`store-N` name, and the public DNS round-robin routes most of them
(`store-2..8`) to a dead CDN edge (`103.235.47.176`) that accepts TCP but
stalls reads, producing `Read timed out. (read timeout=60)` after the job
already reached `done`.

This box pins all eight `store-N` names to a healthy edge in `/etc/hosts`:

```text
182.61.129.11 paddleocr-store-1.bj.bcebos.com
182.61.129.11 paddleocr-store-2.bj.bcebos.com
... (through paddleocr-store-8.bj.bcebos.com)
```

`182.61.129.11` serves every `store-N` name (same bucket/region, verified
0.1 s responses). hosts outranks DNS, so result downloads bypass the dead edge.

Additionally, run OCR with the BOS domain **excluded from the proxy**, because
the local sing-box proxy re-resolves and can still land on the dead edge:

```bash
export NO_PROXY="$NO_PROXY,bj.bcebos.com"
mdocr ocr -f doc.md -o out/
```

Notes:

- `requests` matches `NO_PROXY` by bare domain — `*.bj.bcebos.com` does **not**
  match, use `bj.bcebos.com`.
- Verify health: `curl -sS -o /dev/null -w '%{http_code}\n' --noproxy '*' \
  --resolve 'paddleocr-store-2.bj.bcebos.com:443:182.61.129.11' \
  https://paddleocr-store-2.bj.bcebos.com/` (expect `403`, bucket root).
- To revert: delete the `paddleocr-store-*` lines from `/etc/hosts`.
- If Baidu rotates edges, re-verify the pinned IP before mass OCR runs.
