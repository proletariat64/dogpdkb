# Discover contract

`dogpdkb.discover.discover(settings, book, store, run, listing=None) -> dict`
runs inside the caller's `store.lock()` and uses the caller's Run; sync calls
exactly this function. Optional `listing` is a public `skylark-listing-1` JSON
path for offline operation. Otherwise only `skylark listing --url --output`
runs, through `settings.command('skylark')`; discover never exports bodies.

The return value and `run/differences.json` have schema `dogpdkb-differences-1`,
book (key/name/url), run_id, captured_at, listing (frozen artifact path),
documents (selected rows), absent (IDs), ignored (IDs), and cutoff (first-run
local newest filename date, or null). Each selected row contains document_id,
version (`value`, `precision`, optional normalized `utc`), title, path (remote
TOC path), source_url, format, metadata (public listing row), reasons (strings),
and local (existing document state, including corpus/raw facts if known).
Aliases `name`, `logical_path`, `source_ref`, `type` mirror title/path/source_url/
source type for apply's metadata. A pending fact `{version,reasons,metadata,run_id}`
remains in the document until graph publication acknowledges that version and
metadata. Stage owners clear only matching pending work after final success.

Versions are source observations, never synthetic successful raw/corpus/graph
versions. `normalize_version(value)` is public for checking export reports;
unknown/null remains null. The caller must reject unknown export versions rather
than promote them to exact success. Date-only versions have precision `day`.

CLI: `dogpdkb discover --config FILE --book KEY [--listing JSON]`. Stdout is a
JSON pointer (`run`, `differences`, counts); stderr carries diagnostics. The
complete listing and all validated observations are persisted only after the
whole listing passes its public completeness, identity and source-book checks.
Incomplete listing fails the run without changing the observation baseline.
Absent sources are evidence only; no corpus/graph deletion is performed.
An existing corpus without registered baseline/observation is refused with an
actionable `init-state` instruction, preventing accidental historical backfill.

State observation stores a complete ID map independent of run cleanup. Each
document's observed fact is separate from raw/corpus/graph successes. A historical
missing ID filtered on first discovery stays filtered until its source changes.
An eligible but unattempted version stays eligible on the next discovery even
when its observation is unchanged; stage-specific failed/unknown suppression
remains apply's responsibility, so OCR failure cannot suppress graph compilation.

## Document subscriptions

A source may specify a nonempty unique string `document_ids` list. The complete
Skylark listing is validated before filtering; its advertised count and artifact
are never rewritten. Only subscribed IDs enter observation/selection/state;
missing subscribed IDs appear as absence evidence. Omission means the whole book.
State and paused-run targets pin the scope. Changing it requires a new key/state.
