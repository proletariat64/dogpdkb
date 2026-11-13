"""Manage only the active, installer-created Linux environment.

Configuration and data must live outside disposable virtual environments.
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MARKER = ".dogpdkb-managed"
BASE = "https://code.alipay.com/pd.pt/dogpdkb/releases/download"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_runtime_descriptor = None


@dataclass(frozen=True)
class Installation:
    prefix: Path
    environment: Path
    binary: Path
    config: Path


def locate() -> Installation:
    """Infer identity from this interpreter, never from mutable environment overrides."""
    environment = Path(sys.prefix).absolute()
    prefix = environment.parent.parent
    if (
        environment.is_symlink()
        or environment.resolve() != environment
        or environment.parent.name != "versions"
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[A-Za-z0-9]{6}", environment.name)
        or not (environment / "pyvenv.cfg").is_file()
        or not (prefix / "current").is_symlink()
        or (prefix / "current").readlink() != environment
    ):
        raise ValueError(
            "This command requires the active installer-managed environment."
        )
    marker = environment / MARKER
    if marker.is_symlink():
        raise ValueError("Invalid installation ownership marker.")
    if marker.exists():
        rows = marker.read_text().splitlines()
        if len(rows) != 4 or rows[:2] != ["1", str(prefix)]:
            raise ValueError("Invalid installation ownership marker.")
        binary, config = map(Path, rows[2:])
    else:
        home = Path.home()
        if prefix != home / ".local/share/dogpdkb":
            raise ValueError("Unmarked custom installations cannot be managed safely.")
        binary, config = home / ".local/bin", home / ".config/dogpdkb"
        command = binary / "dogpdkb"
        if (
            not command.is_symlink()
            or command.readlink() != prefix / "current/bin/dogpdkb"
        ):
            raise ValueError("Unmarked installation has no owned command link.")
    for path in (binary, config):
        if not path.is_absolute() or path.resolve() != path or path == Path("/"):
            raise ValueError("Invalid installation paths.")
        if (
            path == environment
            or environment in path.parents
            or path in environment.parents
        ):
            raise ValueError(
                "Configuration and commands must be outside the environment."
            )
        if path == prefix / "versions" or prefix / "versions" in path.parents:
            raise ValueError("Configuration and commands must be outside versions.")
    for name in ("dogpdkb", "mdocr"):
        if not (environment / "bin" / name).is_file():
            raise ValueError("Incomplete managed environment.")
    return Installation(prefix, environment, binary, config)


@contextlib.contextmanager
def locked(installation: Installation, *, shared=False):
    """Keep lock inode even after uninstall; all activation/removal shares it."""
    path = installation.prefix / ".lifecycle.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(
                descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
            )
        except BlockingIOError as error:
            raise ValueError("Another installation operation is running.") from error
        if locate() != installation:
            raise ValueError("The active installation changed; retry from its command.")
        yield descriptor
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def runtime_lock():
    """Keep a managed interpreter available while commands and tools use it."""
    global _runtime_descriptor
    try:
        installation = locate()
    except ValueError:
        yield
        return
    with locked(installation, shared=True) as descriptor:
        _runtime_descriptor = descriptor
        try:
            yield
        finally:
            _runtime_descriptor = None


def runtime_lock_fds():
    return (_runtime_descriptor,) if _runtime_descriptor is not None else ()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Installer redirects are not accepted.")


def download_installer(version: str) -> bytes:
    if not VERSION.fullmatch(version):
        raise ValueError("Version must be MAJOR.MINOR.PATCH.")
    url = f"{BASE}/v{version}/install.sh"
    with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
        body = response.read(256 * 1024 + 1)
    if len(body) > 256 * 1024 or not body.startswith(b"#!/usr/bin/env bash\n"):
        raise ValueError("Release did not return a bounded Bash installer.")
    text = body.decode("utf-8")
    if (
        f"\nVERSION='{version}'\n" not in text
        or f"\nRELEASE_BASE_URL='{BASE}/v{version}'\n" not in text
    ):
        raise ValueError("Installer does not match the requested Release.")
    return body


def update(version: str) -> int:
    installation = locate()
    if not VERSION.fullmatch(version):
        raise ValueError("Version must be MAJOR.MINOR.PATCH.")
    current = importlib.metadata.version("dogpdkb")
    if not VERSION.fullmatch(current):
        raise ValueError("The installed package has no supported stable version.")
    if tuple(map(int, version.split("."))) < tuple(map(int, current.split("."))):
        raise ValueError(
            "Downgrades are not supported; select the current or a newer version."
        )
    with locked(installation) as descriptor:
        body = download_installer(version)
        environment = {
            **os.environ,
            "DOGPDKB_HOME": str(installation.prefix),
            "DOGPDKB_BIN_DIR": str(installation.binary),
            "DOGPDKB_CONFIG_DIR": str(installation.config),
            "DOGPDKB_LOCK_FD": str(descriptor),
        }
        with tempfile.TemporaryFile() as script:
            script.write(body)
            script.seek(0)
            return subprocess.call(
                ["bash"], stdin=script, env=environment, pass_fds=(descriptor,)
            )


def uninstall(yes: bool = False) -> int:
    installation = locate()
    with locked(installation):
        print(f"Remove environment: {installation.environment}")
        print(f"Remove owned command links in: {installation.binary}")
        print(f"Keep configuration: {installation.config}; keep all external data.")
        if not yes:
            if not sys.stdin.isatty():
                raise ValueError("Noninteractive uninstall requires --yes.")
            if input("Uninstall dogpdkb? [y/N] ").strip().lower() != "y":
                return 1
        for name in ("dogpdkb", "mdocr"):
            link = installation.binary / name
            if (
                link.is_symlink()
                and link.readlink() == installation.prefix / "current/bin" / name
            ):
                link.unlink()
        (installation.prefix / "current").unlink()
        try:
            shutil.rmtree(installation.environment)
        except OSError as error:
            raise OSError(
                f"Could not remove {installation.environment}: {error}"
            ) from error
    return 0
