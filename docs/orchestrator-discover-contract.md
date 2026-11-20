# Discover contract

`dogpdkb.discover.discover(settings, book, store, run, listing=None, *, sources=None)`
runs inside the caller's store lock and uses the caller's Run. `sync` calls the
same function. Both CLI commands accept repeatable `--source KEY`; omission scans
all enabled sources of the selected subscription. Sync can instead take `--dir
PATH`; it passes the resolved directory as the only source to this same scanner.
Registering an ad hoc binding alone implies neither a listing nor acquired bytes.

## Selected source scans

Yuque listing uses `skylark listing --url --output`, through the configured
public CLI. `discover --listing PATH` may supply a complete `skylark-listing-1`
JSON artifact instead when exactly one Yuque source is selected. Discovery
never exports Yuque bodies. URLs, complete flag, source IDs, TOC and advertised
counts are validated before observations or a runnable plan are committed.

A directory scan recursively discovers regular Markdown files; it excludes
hidden entries, `_inventory` and symlinks. A directory must not overlap managed
paths. Its document ID is the root-relative POSIX path and provenance is the file
URI. The public `mdcompare catalog` identifies references on the original page.
Referenced local files inside the source root are hashed with the Markdown and
copied in the same relative layout; a resolved resource outside the source root
or through a symlink is rejected. Remote resources are not fetched by discovery.
Selected inputs are verified while freezing; originals are not modified. A
separate `apply --input` stages its own copy so a paused apply remains usable
after purging the completed discovery run.

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

A Yuque source accepts `include_document_ids` (allowlist),
`exclude_document_ids` (blocklist), or neither (whole book). Each list contains
unique nonblank string IDs and must not be empty. The old source-level
`document_ids` is an alias for `include_document_ids`; combining any of the
three fields is rejected before discovery. The full listing is validated and
frozen before filtering, without rewriting its counts. Only eligible IDs enter
observations and candidate work. A known document filtered out by policy is not
a new absence; its earlier observations and publication facts are preserved.
When re-admitted, it compares against its last recorded observation if present.

A fresh scan applies the current selection policy. Paused runs keep the saved
policy and inputs. Changing only the lists does not rebind the durable source
identity (source key, type and URL). Changing the URL or source type still
requires a new source key. Saved binding JSON keeps `document_ids` as the
inclusion field for compatibility, plus `exclude_document_ids` when configured.
