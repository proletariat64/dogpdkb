# Discover contract

`dogpdkb.discover.discover(settings, book, store, run, listing=None, *, sources=None)`
runs inside the caller's store lock and uses the caller's Run. `sync` calls the
same function. Both CLI commands accept repeatable `--source KEY`; omission scans
all enabled sources of the selected subscription.

## Selected source scans

Yuque listing uses `skylark listing --url --output`, through the configured
public CLI. `discover --listing PATH` may supply a complete `skylark-listing-1`
JSON artifact instead when exactly one Yuque source is selected. Discovery
never exports Yuque bodies. URLs, complete flag, source IDs, TOC and advertised
counts are validated before observations or a runnable plan are committed.

A directory scan recursively discovers regular Markdown files; it excludes
hidden entries, `_inventory` and symlinks. A directory must not overlap managed
paths. Its document ID is the root-relative POSIX path, version is the content
SHA-256, and provenance is the file URI. Changed selected bytes are frozen in
wash; if the bytes change while freezing the scan fails. Metadata and path alone
are not proof of a successful publication. The source files are not modified.

If any selected scan fails, no observations are committed and no combined
executable differences are saved. Unselected sources are untouched. Missing
IDs are evidence only and never trigger corpus or graph deletion.

## Differences and durable observation

The return value and `differences.json` use `dogpdkb-differences-2`:
`book` (key/name/bindings), `run_id`, `captured_at`, `sources` (selected ordered
keys), `listings` (Yuque source key to frozen listing), `documents`, `absent`,
`ignored`, and `cutoffs` (source key to initial date or null). Applicable fields
on rows include scoped `document_id`, `source_key`, `source_document_id`,
`version`, `title`, `path`, `source_url`, `format`, `metadata`, `reasons`, and
`local` prior facts. Directory rows include `frozen_input`. Descriptor aliases
include `name`, `logical_path`, `source_ref` and `type`.

Every source keeps its own complete observation. The recorded document identity
combines its source key and its underlying ID. The source ID is kept separately
and is the value used when talking to Skylark. Observed versions are not
successful raw/corpus/graph versions. Unknown Yuque versions remain null.
Stage owners acknowledge only matching pending work after actual success or an
intentional comparison decision. An eligible but unattempted version stays
eligible; duplicate failed or unknown work is suppressed by the relevant stage.

An existing Yuque corpus with unregistered numeric filenames requires
`init-state` before initial scanning of the eligible source. The source's
initial cutoff excludes older historical gaps. A historical missing ID filtered
on first discovery stays filtered until it changes.

## Document subscriptions

A Yuque source may specify nonempty unique string `document_ids`. Omission
subscribes to the whole book. The complete listing is validated and frozen first;
its counts are not rewritten. Only the selected IDs enter observation, state,
and candidate work. Changing the scope requires a new source key; saved runs
pin bindings and publication targets.
