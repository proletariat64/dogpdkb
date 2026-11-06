# Product homepage delivery

The customer-facing homepage is
`http://poolab-iys595a7znqgza1e.inc.alipay.net/kb/`, served by the existing Nginx
root at `/home/admin/webserver/site/kb`. `poolab` uses the `default` connection.

## UI ownership

`web/kb/` contains the existing homepage HTML, stylesheet and catalog JavaScript,
plus independent `release.css` and `release.js`. The installation panel reuses the
sage background, dark-green terminal and typography of the knowledge interface.
It appears below the hero, with Linux support, version, copy command and an
AntCode Releases link. The navigation includes an installation anchor.

The deployment is additive. Back up existing `index.html`, deploy `release.css`
and `release.js` first, then atomically replace `index.html` from this checkout.
The imported `style.css` and `app.js` match the original deployed homepage; no
changes to them are needed for this feature. Keep per-file backups outside the
served directory so rollback can restore the previous HTML/assets.

Do not recursively sync `web/kb/` over the whole deployed directory, remove
untracked remote files, or replace `catalog.json` or `map/`. Knowledge snapshots
have separate ownership. No Nginx reload is needed for this static-file update.

## Release ownership

The UI reads `release.json` with `cache: no-store`, independently of the corpus.
`make release` first finalizes its AntCode Release, then calls
`make publish-site-release TAG=vX.Y.Z`. The helper:

1. Validates exact tag, stable/published/anonymous/unblocked state and attachments.
2. Fetches the anonymous HTTPS installer and validates its version, wheel and URL.
3. Sends only small metadata via `poolab` to a unique staging directory.
4. Holds a lock and atomically replaces `/kb/release.json`; refuses stable downgrades.
5. Confirms file and HTTP readback, rolling back to prior bytes on verification
   failure, then cleans its own staging directory.

A failure is not reported as a successful release workflow. `make release RETRY=1`
keeps the existing version and retries publication plus homepage update. For a
site-only repair, use `make publish-site-release TAG=vX.Y.Z`. Neither path edits
HTML, knowledge data, Nginx, installed programs or services.

Metadata fields: `schema_version` (1), `version`, `tag`, `channel` (`stable`),
`installer_url`, `release_url`, `published_at`. The UI constructs the shell command
itself; it never accepts an arbitrary command from JSON. It validates the precise
AntCode HTTPS URL and canonical version/tag before enabling copying.

Before the first formal Release, the homepage may explicitly show the already
verified `installer-uat-20261106T060938Z` as **预览版 v0.1.0**, `channel: preview`.
It must never call that UAT package a stable Release. The first real release
replaces preview metadata automatically. No synthetic stable release is created
just to populate the homepage. Published installation commands remain version-bound.

On missing, invalid or unavailable release metadata, the command is disabled and
the AntCode Releases link remains usable. On plain HTTP or clipboard permission
denial, copying falls back to selecting the command and the browser copy command;
if that also fails, the UI instructs the visitor to copy manually. Mobile layouts
wrap the command without adding newline characters to the copied value.

## Verification

`make check` includes the Python publication/lifecycle tests and the standalone
JavaScript component tests when Node.js is installed. Run the latter directly:

```bash
node --test tests/site_release.test.cjs
```

Browser acceptance checks: desktop/mobile layout and horizontal overflow, HTTP
copy fallback, exact command contents, preview/stable distinction, Releases link,
metadata failure isolation, and existing document search. Production verification
must not run a sync, OCR job, graph rebuild or install into an existing user prefix.
The homepage is currently HTTP; it is not a substitute for transport protection.
The installer itself and its wheel are downloaded from AntCode over HTTPS.
