# Local releases from merged GitHub source

## Prepare the version in a normal PR

GitHub `proletariat64/dogpdkb` is the source repository. Development, PR checks
and merges stay on GitHub, whose default branch is `master`. AntCode
`pd.pt/dogpdkb` hosts installation artifacts by default. Alternatively, publish
artifacts to the GitHub source repository with `TARGET=github`.

If a new version is needed, edit `project.version` in `pyproject.toml` and run
`uv lock` in the normal development PR. Merge that PR after its checks pass.
Then switch to `master`, pull the merged source and release locally:

```bash
git switch master
git pull --ff-only origin master
make release DRY_RUN=1
make release
```

`make release` publishes the existing project version. It never increments a
version, edits version files, stages files, creates a version commit, pushes a
branch or mirrors source to AntCode. Optional `VERSION=0.2.0` only asserts that
the merged project version is already `0.2.0`.

Everything runs locally using the existing AntCode CLI login, including keyring
credentials. The command:

1. Requires trusted GitHub `origin`, clean checkout and attached `master`.
2. Fetches GitHub `master` and requires HEAD to match the fetched commit exactly.
3. Rejects conflicting or already existing version tags unless explicitly retrying.
4. Runs `make bootstrap` to refresh the local editable install and locked
   dependencies, then `make check` and `make smoke` before creating a tag.
5. Creates an annotated `v<project.version>` tag and pushes only that tag to GitHub.
6. Publishes anonymous installation attachments through the local AntCode CLI.
7. Verifies the published installer and updates the homepage's `dogpdkb.json`
   through `poolab`.

Dry run reports the current version and planned checks without fetching,
running checks or performing network writes. Actual release requires GitHub
tag-write permission, AntCode Release-write permission, Python 3.12, uv, Git,
and a working `poolab` default connection with homepage write access. There is
no cloud publishing job or separate publishing token requirement.

Bootstrap also runs on `RETRY=1`, so pulling a version bump cannot leave the
development environment reporting the previous `dogpdkb`/`mdocr` version during
checks. It reuses the existing environment and retains extra installed packages.

## Choose the release target

```bash
make release                       # default: antcode CLI
make release TARGET=antcode         # explicit AntCode target
make release TARGET=github          # gh CLI, using its existing login
make release TARGET=github DRY_RUN=1
make release TARGET=github RETRY=1   # also use when the tag already exists on GitHub
```

The Python entry points accept `--target antcode|github`, defaulting to
`antcode`. For upload only, use `make publish-release TAG=v0.1.1 TARGET=github`.
The GitHub destination is `proletariat64/dogpdkb`; AntCode keeps its existing
`pd.pt/dogpdkb` default and optional `--project` override. `--require-token`
remains AntCode-only; authenticate GitHub with `gh auth login`.

Both targets use the same merged-source/tag checks, draft-first publication,
canonical remote package checksums, immutable attachments and retry behavior.
GitHub publication uses `gh api` for release metadata and `gh release` for
creation, upload, download and finalization. Its installer points to GitHub
release downloads. GitHub access follows repository visibility; private-repo
assets are not anonymously downloadable by the curl installer.

GitHub installations are upgraded by rerunning the target version's GitHub
`install.sh`, preserving any custom installation paths. `dogpdkb update` currently
uses AntCode only and cannot fetch GitHub-only versions; see the
[GitHub upgrade command](installing.md#upgrade-and-retry).

The homepage helper is AntCode-specific. GitHub releases skip that step and
need neither AntCode CLI nor `poolab`; the existing AntCode homepage remains
managed by the default release flow. Retry with the same `TARGET` to resume
that provider. To publish the same source version to the other provider, use
its target together with `RETRY=1`, since the source tag already exists.

## Source identity and artifact hosting

The local source tag and its remote GitHub tag must both resolve to HEAD.
Release descriptions and `RELEASE_NOTES.md` identify the source explicitly:

```text
Source-Commit: <GitHub commit SHA>
Source-URL: https://github.com/proletariat64/dogpdkb/commit/<SHA>
Source-Tag: https://github.com/proletariat64/dogpdkb/tree/v<version>
```

AntCode requires a revision in its own repository when creating a Release.
The publisher reads its existing `main` commit and uses that revision only as
an artifact-host carrier. This does not copy source, push branches or synchronize
GitHub tags into the AntCode source history. AntCode's revision and any
automatically generated source archive are not the dogpdkb source identity; use
the uploaded wheel/source archive and the GitHub provenance above.

Existing releases must have exactly the same source commit marker and source
URLs before resume or no-op. An unrelated test or old release with the same
name is rejected without mutation. A retry reuses the existing release's carrier;
it does not require AntCode `main` to remain at its creation-time commit.

## Checks and published files

CI checks source changes; publishing remains local. GitHub PR checks do not
publish installation packages. AntCode has no source CI in this workflow.

The Release contains a wheel, source archive, version-bound `install.sh`,
`SHA256SUMS` and `RELEASE_NOTES.md`. Notes include source provenance and up
to eight visible commit subjects.

The generated installer also installs `pi-skylark@latest` with npm into its
managed environment and exposes `skylark` alongside the Python commands. Node.js
and npm must already be installed on the destination. Skylark is fetched at
installation time, including on same-version reinstalls; it is not pinned or
added as a Release attachment. npm/startup failure leaves the old environment
active. No separate Skylark source build or publication is needed.

Publication starts as a draft and finalizes after the expected files are present.
AntCode Releases are deliberately anonymous: metadata and installation attachments can be
downloaded without an AntCode account. This does not change repository visibility;
anonymous access is not itself an internal-network restriction. Never package
credentials, production configuration or corpus data.

Existing tags and attachments are never replaced or force-pushed. A completed,
anonymous Release with all expected attachments and matching source provenance
is left unchanged when retried.

`install.sh` is rendered from `scripts/install.sh`, with the exact version, HTTPS
Release URL, wheel filename and SHA256 of canonical remote wheel bytes. Its own
checksum is also listed in `SHA256SUMS`. No API token or CLI login is embedded.

## Retry

If a tag push, upload or homepage update fails:

```bash
make release RETRY=1
```

Retry keeps the merged version unchanged and reruns local checks/build. Existing
local or GitHub tags must resolve to HEAD. A missing local tag can be recreated;
a successful remote tag push is skipped. No branch is pushed and no version
commit is created. Do not move `master` away from the version being retried.

If the Release exists but the homepage update failed, retry still performs the
homepage step after the immutable Release no-op; it does not create a new version.

Previously uploaded packages remain canonical. Retry downloads those bytes when
generating checksums, so different archive timestamps in a rebuild do not cause
replacement. Same-source release notes remain canonical. Conflicting generated
metadata stops rather than being overwritten.

For upload only, from a clean checkout of the existing GitHub version tag with
artifacts already built:

```bash
make publish-release TAG=v0.1.1
```

To repair only homepage metadata after a successful Release:

```bash
make publish-site-release TAG=v0.1.1
```

The homepage helper accepts only published, anonymous, unblocked stable Releases
whose installer identifies that exact version and URL. It atomically replaces
only `/home/admin/webserver/site/releases/dogpdkb.json`, verifies readback and HTTP view,
and refuses to move an existing stable homepage to an older version. Failed
post-write verification restores previous metadata while holding the same lock,
or removes the new file on first publication. It never edits the knowledge
catalog, graph, Nginx or installed program.

See [homepage delivery](homepage.md) for UI deployment and preview behavior.
For server installation and upgrades, see [the curl installer](installing.md).

Local build and installation smoke checks default to offline uv cache reuse.
They reuse the selected Python and preserve the real uv cache even when testing
with a temporary HOME. Every smoke installation uses the exported lockfile
constraints. Set `DOGPDKB_PYPI_INDEX` to select a trusted mirror; when explicitly
preparing missing cache entries, `UV_OFFLINE=0 make smoke` permits downloads.
The installer requires an existing Python (default 3.12); use `DOGPDKB_PYTHON`
to select it. It does not download Python.
