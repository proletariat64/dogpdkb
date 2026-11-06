# Linux installation and upgrades

## One command

After a version has been published, use its exact Release tag. For example,
replace `v0.1.1` below with an existing version, not an installer-test tag:

```bash
curl -fsSL https://code.alipay.com/pd.pt/dogpdkb/releases/download/v0.1.1/install.sh | bash
```

The Release and its attachments are anonymous. The server does not need an
AntCode CLI, account, token, Git checkout or sudo. Linux, Bash, curl, sha256sum
and standard GNU file utilities are required. As with any curl-to-shell installer,
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
- Command links: `~/.local/bin/dogpdkb` and `~/.local/bin/mdocr`.
- Example configuration: `~/.config/dogpdkb/dogpdkb.toml` and `books.json`.

The installer prints a PATH command if `~/.local/bin` is not already on PATH;
it never edits your shell startup files. Package-only doctor runs before activation.
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

Stop an active sync, then run the same curl command with the new version's tag.
A fresh virtual environment is built and checked before an atomic `current`
symlink switch. A failed download, checksum, package install or doctor leaves the
previous working version selected. A successful reinstall of the same version
also builds a fresh environment; no files in the old environment are changed.

Configuration and data are outside the versioned environments and are never
replaced. If either configuration file already exists, including a symlink, both
are left alone, even if setup is partial. Old environments remain for inspection;
there is no automatic data cleanup. An unrelated command occupying the chosen
bin path, or an unmanaged `current` path, causes refusal instead of overwrite.
There is no automatic service restart, schema migration or uninstall in this scope.

Override directories with `DOGPDKB_HOME`, `DOGPDKB_BIN_DIR`, and
`DOGPDKB_CONFIG_DIR` on the **bash** side of the pipeline. They must be absolute.
`DOGPDKB_PYTHON` selects another supported interpreter (the package requires
Python 3.10+); normal uv network/cache/mirror variables remain usable. Do not run
installers concurrently against the same command and configuration directories.
