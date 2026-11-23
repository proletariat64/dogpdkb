# Rust mdocr experiment

Build with Rust 1.95 or later. For a static x86-64 Linux executable, run this
from the repository root on x86-64 Linux:

```sh
RUSTFLAGS="-C target-feature=+crt-static" cargo build --locked --release \
  --target x86_64-unknown-linux-gnu --manifest-path experiments/mdocr-rust/Cargo.toml
```

The output is `experiments/mdocr-rust/target/x86_64-unknown-linux-gnu/release/mdocr`.
The binary embeds its image codecs, SVG renderer, fonts, TLS roots, and Mermaid
parser. Use `mdocr licenses` for the embedded font notices.

```sh
mdocr plan --mode paddle --input source.md --output enriched.md --plan plan.json
mdocr run --plan plan.json --result result.json
```

- **Plan** accepts a Markdown file, a JSON path list, or a directory. Use
  `--download` to stage selected HTTP(S) image bytes and `--download-list` to
  restrict staging to named documents. The plan freezes Markdown and image
  hashes before any OCR calls.
- **Run** prepares images, calls the selected OCR provider, and writes the
  `mdocr-result-2` report and plan-bound Markdown evidence. Resume using the
  same plan and result. A request with an unknown outcome stays in the
  journal. Automatic replay is limited to the documented, explicitly configured
  batch fix exception for typed connection interruptions.
- **Assemble** reads a running journal and recovers already accepted patches
  offline. Pass the producing runtime's version and a fresh output directory.
- **Check Mermaid** validates fenced diagrams in a file or stdin.

Paddle uses `PADDLEOCR_TOKEN` and the configured PaddleOCR job endpoint.
LLM uses the saved Pi OAuth entries for `openai/…` or `openai-codex/…`.
It reads and refreshes credentials natively. Use `PI_CODING_AGENT_DIR` to
locate the OAuth store when it lives outside `~/.pi/agent`.
Both provider modes need network access for OCR.

The Rust renderer supports SVG shapes and text with embedded Latin and CJK
fonts. SVGs with HTML `foreignObject` labels, missing glyphs, or unsupported
embedded resources are held before sending. Raster support covers PNG, JPEG,
WebP, GIF, BMP, ICO, and TIFF.

The experiment is opt-in. Use Rust with a fresh plan or one with no previous
admissions. Continue an in-flight execution with its original runtime. An
explicit offline assembly can validate historical request keys with the
original runtime version.
