# Engineering document and CI rules

## Commands

After `make bootstrap`, run `make check` before pushing. It runs these gates,
stopping with a nonzero exit code if any gate fails:

- `make doc-guard`: document format, naming, and placement.
- `make lint`: Ruff code checks and formatting verification.
- `make test`: offline pytest tests.

These commands only check; they do not rewrite files, synchronize the corpus,
call OCR services, rebuild graphs, or publish data.

## Document scope and placement

The guard uses Git's file list, including tracked Markdown and new non-ignored
Markdown files. It never recursively scans the ignored corpus. Deleted documents
are skipped; document symlinks are rejected. Accidentally tracking corpus Markdown
fails the placement rules instead of silently exempting it.

- Engineering documents belong in `docs/`.
- Local tickets and specs may use `.scratch/`, as described in
  [the issue tracker conventions](agents/issue-tracker.md).
- Root documents are limited to `README.md`, `AGENTS.md`, `GLOSSARY.md`,
  `GLOSSARY-MAP.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `LICENSE.md`, and `LEGAL.md`.
- `skylark_books/roots.md` is the existing source-reference exception.
- Under `docs/` and `.scratch/`, directories and filenames use lowercase
  kebab-case and the `.md` extension, e.g. `docs/spec/mdocr.md` or
  `.scratch/feature/issues/01-add-check.md`.

## Markdown format

- Use UTF-8 without a byte-order mark and LF line endings.
- End with exactly one newline.
- Use exactly one ATX H1 title (`# Title`), as the first nonblank line.
- Close backtick and tilde code fences with a matching marker of sufficient length.
- Outside fenced code, use spaces instead of tabs and no trailing whitespace.
  Exactly two trailing spaces are allowed for Markdown hard line breaks.

The guard is intentionally a small structural check, not a complete Markdown
parser. It does not enforce line length, heading language, link availability,
or the factual accuracy of document content. No remote links are fetched.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes, pull requests, and manual dispatch.
It bootstraps the locked development dependencies from public PyPI, then runs
`make check` followed by `make smoke` (build and isolated platform installation).
The project explicitly selects PyPI so the committed lockfile does
not depend on a developer's internal package mirror. Bootstrap requires network
access on a cold cache; the document, lint, and test gates themselves are offline.
The job has read-only repository permissions and a timeout; it receives no
production credentials.

CI runs after a push, not before a local Git commit. No local Git hooks are
installed. Repository administrators must separately require the `check`
status in branch protection/rulesets if failed checks should block merges.
