# Linux installation and upgrades

## One command

After a version has been published, use its exact Release tag. For example,
replace `v0.1.1` below with an existing version, not an installer-test tag:

```bash
curl -fsSL https://code.alipay.com/pd.pt/dogpdkb/releases/download/v0.1.1/install.sh | bash
```

The Release and its attachments are anonymous. The server does not need an
AntCode CLI, account, token, Git checkout or sudo. Linux, Bash, curl, sha256sum,
flock, Node.js, npm and standard GNU file utilities are required. As with any curl-to-shell installer,
trust the HTTPS origin and inspect the script first if required by your policy.
Use `bash -o pipefail -c 'curl -fsSL <URL> | bash'` in automation when download
failure must also determine the enclosing shell's exit status.

The version-specific script verifies the wheel's embedded SHA256, reuses uv if
available, or installs uv 0.11.7 under its own tools directory without changing
shell profiles. uv provisions Python 3.12 when needed. Dependency downloads default
to the company mirror, `https://pypi.antfin-inc.com/simple/`; uv/Python bootstrap
also needs access to Astral/GitHub. Other trusted mirrors can be selected:

```bash
curl -fsSL <release-install.sh-URL> | DOGPDKB_PYPI_INDEX=https://pypi.org/simple bash
```

The unrendered source `scripts/install.sh` intentionally refuses to install.
Only the published version-bound script has a package URL and checksum.

## Files and first use

Defaults:

- Programs and versioned virtual environments: `~/.local/share/dogpdkb/`.
- Command links: `~/.local/bin/dogpdkb`, `~/.local/bin/mdocr` and
  `~/.local/bin/mdcompare` and `~/.local/bin/skylark` (or the selected
  `DOGPDKB_BIN_DIR`). Installing or upgrading adds the missing command links.
- Example configuration: `~/.config/dogpdkb/dogpdkb.toml` and `books.json`.

The installer prints a PATH command if `~/.local/bin` is not already on PATH;
it never edits your shell startup files. Package-only doctor runs before activation.
Each install, upgrade or same-version reinstall runs `npm install pi-skylark@latest`
inside the candidate managed environment. npm uses its configured registry; no
Skylark version is pinned. The command is checked before activation. A download,
installation or startup failure preserves the previous active environment.
The installer also installs `mermaid@12.3.0` and `jsdom@26.1.0` in the candidate
environment and verifies Mermaid parsing before activation. Managed `mdocr`
works without setting `MDOCR_NODE_DEPENDENCIES`.
Uninstall removes this managed Skylark copy and its owned link; Node.js, npm and
unrelated global npm installations remain untouched. Gateway/SSO setup is separate.
Source examples are disabled. Edit the gateway, source selection and data paths
before syncing, then run:

```bash
~/.local/bin/dogpdkb doctor --config ~/.config/dogpdkb/dogpdkb.toml
```

Commands retain their configuration contract: explicit `--config` or
`DOGPDKB_CONFIG` selects the instance; otherwise a wheel installation reads
`./dogpdkb.toml`. Installation does not silently change that default. Template data
paths are relative to the configuration directory until you edit them. No sync,
OCR, graph rebuild, Nginx configuration, scheduler or service is started.
Graphify and operational authentication remain separate prerequisites; see
[platform setup](platform.md).

## Upgrade and retry

Stop active sync/OCR/graph jobs before upgrading or uninstalling. From an
AntCode installer-managed deployment starting with v0.1.1:

```bash
dogpdkb update --version 0.1.2  # example: must already be published
```

The version is explicit: this command does not guess the latest release or require
an AntCode login. It downloads the matching installer from the fixed AntCode HTTPS Release
origin and uses the same installation flow. A v0.1.0 installation does not have the
new commands; run the newer version's curl installer once to upgrade it.

For GitHub releases, upgrade by running the target version's GitHub installer:

```bash
# Example: v0.1.2 must already be published with public installation assets.
bash -o pipefail -c 'curl -fsSL https://github.com/proletariat64/dogpdkb/releases/download/v0.1.2/install.sh | bash'
```

`dogpdkb update` does not infer the installation's provider: it always uses AntCode,
so it cannot retrieve GitHub-only versions. The GitHub installer uses the same
upgrade flow described below. For a custom installation, pass the original
`DOGPDKB_HOME`, `DOGPDKB_BIN_DIR` and `DOGPDKB_CONFIG_DIR` to the installer again;
otherwise it targets the default paths. Keep any required `DOGPDKB_PYPI_INDEX`
override as well.

A fresh virtual environment is built and checked before an atomic `current`
symlink switch. A failed download, checksum, package install or doctor leaves the
previous working version selected. After successful activation the previous managed
environment is deleted, including on a same-version reinstall. This is a complete
installation, not a delta patch. There is no version-history retention or rollback
command. An interruption after activation may leave an old directory behind;
unknown directories are never swept just because they are under `versions/`.

Configuration and user data must remain outside the disposable virtual environments.
They are never replaced or deleted by lifecycle commands. If either configuration
file already exists, including a symlink, both are left alone even if setup is partial.
An unrelated command occupying the chosen bin path, or an unmanaged `current` path,
causes refusal instead of overwrite. Do not store documents or configuration inside
a versioned virtual environment: that directory contains disposable program files.
There is no automatic service restart or schema migration.

## Uninstall

```bash
dogpdkb uninstall        # interactive confirmation
dogpdkb uninstall --yes  # automation; no prompt
```

Uninstall removes only the managed installation and its matching command links.
It preserves configuration, corpus/graph/Web data, unrelated files and links,
system Python, and shared uv tools/caches. It does not remove the served homepage.
An arbitrary pip/uv virtual environment or source checkout is not an installer-owned
deployment; use its original environment manager instead. Removing the executable
does not stop existing jobs, so stop them first.

## Custom installation paths

Override directories with `DOGPDKB_HOME`, `DOGPDKB_BIN_DIR`, and
`DOGPDKB_CONFIG_DIR` on the **bash** side of the installation pipeline. They must be
absolute. New installations record these paths so lifecycle commands can locate
them without re-exporting the variables. Legacy custom installations without this
record are refused rather than guessing ownership. Upgrade those into a fresh
managed prefix using separate command paths and the existing configuration directory;
remove the old deployment separately using its original installation method.
`DOGPDKB_PYTHON` selects another supported interpreter (the package requires
Python 3.10+); normal uv network/cache/mirror variables remain usable. Lifecycle
operations serialize changes to an installation; do not share the same command
or configuration directories between independent installation prefixes.
