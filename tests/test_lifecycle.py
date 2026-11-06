"""Lifecycle identity, download, locking and data-preservation regression tests."""

import fcntl
import io
import os
from http.client import HTTPMessage
from pathlib import Path
from urllib.request import Request

import pytest

from dogpdkb import cli, lifecycle
from tests.test_install import Installer


@pytest.fixture
def managed(tmp_path, monkeypatch):
    installer = Installer(tmp_path)
    assert installer.run().returncode == 0
    monkeypatch.setattr(lifecycle.importlib.metadata, "version", lambda name: "0.1.0")
    monkeypatch.setenv("HOME", str(installer.home))
    monkeypatch.setattr(
        lifecycle.sys, "prefix", str((installer.prefix / "current").resolve())
    )
    return installer


def test_uninstall_keeps_config_data_and_unrelated_paths(managed):
    active = (managed.prefix / "current").resolve()
    unrelated = managed.prefix / "versions/unrelated"
    unrelated.mkdir()
    (unrelated / "user.md").write_text("keep")
    data = managed.prefix / "corpus"
    data.mkdir()
    (data / "user.md").write_text("keep")
    mdocr = managed.binary.parent / "mdocr"
    mdocr.unlink()
    mdocr.symlink_to("/other/tool")
    assert lifecycle.uninstall(yes=True) == 0
    assert not active.exists()
    assert not (managed.prefix / "current").is_symlink()
    assert not managed.binary.is_symlink()
    assert mdocr.readlink() == Path("/other/tool")
    assert (managed.config / "books.json").is_file()
    assert (data / "user.md").read_text() == "keep"
    assert (unrelated / "user.md").read_text() == "keep"
    assert (managed.prefix / ".lifecycle.lock").is_file()


def test_uninstall_requires_confirmation(managed, monkeypatch):
    monkeypatch.setattr(lifecycle.sys, "stdin", io.StringIO())
    with pytest.raises(ValueError, match="--yes"):
        lifecycle.uninstall()
    assert managed.binary.exists()


def test_lock_blocks_uninstall_and_installer(managed):
    installation = lifecycle.locate()
    with lifecycle.locked(installation):
        with pytest.raises(ValueError, match="Another"):
            lifecycle.uninstall(True)
        result = managed.run("0.1.1")
        assert result.returncode != 0
        assert "Another installation operation" in result.stderr
    assert managed.version() == "dogpdkb 0.1.0"


def test_unmanaged_interpreter_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle.sys, "prefix", str(tmp_path))
    with pytest.raises(ValueError, match="installer-managed"):
        lifecycle.uninstall(True)


def test_legacy_default_is_supported(managed):
    installation = lifecycle.locate()
    (installation.environment / lifecycle.MARKER).unlink()
    assert lifecycle.locate() == installation
    assert managed.run("0.1.1").returncode == 0
    assert not installation.environment.exists()


@pytest.mark.parametrize(
    "damage",
    ["json", "config-in-env", "symlink-marker", "symlink-env", "symlink-versions"],
)
def test_invalid_identity_is_refused(managed, damage):
    installation = lifecycle.locate()
    marker = installation.environment / lifecycle.MARKER
    if damage == "json":
        marker.write_text('{"prefix": "/"}')
    elif damage == "config-in-env":
        marker.write_text(
            f"1\n{installation.prefix}\n{installation.binary}\n{installation.environment}/data\n"
        )
    elif damage == "symlink-marker":
        marker.unlink()
        marker.symlink_to(managed.config / "books.json")
    elif damage == "symlink-env":
        target = installation.prefix / "outside"
        installation.environment.rename(target)
        installation.environment.symlink_to(target)
    else:
        root = installation.prefix / "versions"
        root.rename(installation.prefix / "elsewhere")
        root.symlink_to(installation.prefix / "elsewhere")
    with pytest.raises(ValueError):
        lifecycle.uninstall(True)
    assert (managed.config / "books.json").exists()


def test_custom_paths_rediscovered_without_environment(tmp_path, monkeypatch):
    installer = Installer(tmp_path)
    prefix, binary, config = (
        tmp_path / name for name in ("prefix", "commands", "settings")
    )
    installer.env.update(
        DOGPDKB_HOME=str(prefix),
        DOGPDKB_BIN_DIR=str(binary),
        DOGPDKB_CONFIG_DIR=str(config),
    )
    assert installer.run().returncode == 0
    monkeypatch.setattr(lifecycle.sys, "prefix", str((prefix / "current").resolve()))
    for key in ("DOGPDKB_HOME", "DOGPDKB_BIN_DIR", "DOGPDKB_CONFIG_DIR"):
        monkeypatch.delenv(key, raising=False)
    installation = lifecycle.locate()
    assert installation == lifecycle.Installation(
        prefix, installation.environment, binary, config
    )
    assert lifecycle.uninstall(True) == 0
    assert (config / "books.json").exists()


def test_update_uses_owned_paths_and_hands_lock_to_installer(managed, monkeypatch):
    installation = lifecycle.locate()
    monkeypatch.setattr(
        lifecycle, "download_installer", lambda version: b"#!/usr/bin/env bash\n"
    )

    def execute(command, *, stdin, env, pass_fds):
        assert command == ["bash"]
        assert stdin.read() == b"#!/usr/bin/env bash\n"
        assert env["DOGPDKB_HOME"] == str(installation.prefix)
        assert env["DOGPDKB_BIN_DIR"] == str(installation.binary)
        assert env["DOGPDKB_CONFIG_DIR"] == str(installation.config)
        assert pass_fds == (int(env["DOGPDKB_LOCK_FD"]),)
        other = os.open(installation.prefix / ".lifecycle.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
        return 0

    monkeypatch.setattr(lifecycle.subprocess, "call", execute)
    monkeypatch.setenv("DOGPDKB_HOME", "/unrelated")
    assert lifecycle.update("0.1.1") == 0


@pytest.mark.parametrize(
    "body",
    [
        b"<html>login</html>",
        b"#!/bin/sh\n",
        b"#!/usr/bin/env bash\n" + b"x" * (256 * 1024),
        b"#!/usr/bin/env bash\nVERSION='9.0.0'\n",
    ],
)
def test_download_rejects_invalid_installers(monkeypatch, body):
    class Opener:
        def open(self, url, timeout):
            assert url == lifecycle.BASE + "/v0.1.1/install.sh"
            assert timeout == 30
            return io.BytesIO(body)

    monkeypatch.setattr(
        lifecycle.urllib.request, "build_opener", lambda handler: Opener()
    )
    with pytest.raises(ValueError):
        lifecycle.download_installer("0.1.1")


def test_download_exact_version_and_reject_redirect(monkeypatch):
    body = f"#!/usr/bin/env bash\nVERSION='0.1.1'\nRELEASE_BASE_URL='{lifecycle.BASE}/v0.1.1'\n".encode()

    class Opener:
        def open(self, url, timeout):
            return io.BytesIO(body)

    monkeypatch.setattr(
        lifecycle.urllib.request, "build_opener", lambda handler: Opener()
    )
    assert lifecycle.download_installer("0.1.1") == body
    with pytest.raises(ValueError, match="Version"):
        lifecycle.download_installer("../../other")
    with pytest.raises(ValueError, match="redirect"):
        lifecycle.NoRedirect().redirect_request(
            Request("https://code.alipay.com"),
            io.BytesIO(),
            302,
            "",
            HTTPMessage(),
            "https://evil.test",
        )


def test_update_executes_installer_with_inherited_lock(managed, monkeypatch):
    previous = (managed.prefix / "current").resolve()
    managed.env["TEST_VERSION"] = "0.1.1"
    for key, value in managed.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        lifecycle,
        "download_installer",
        lambda version: managed.render(version).read_bytes(),
    )
    assert lifecycle.update("0.1.1") == 0
    assert not previous.exists()
    assert managed.version() == "dogpdkb 0.1.1"


def test_update_rejects_downgrade_before_download(managed, monkeypatch):
    monkeypatch.setattr(lifecycle.importlib.metadata, "version", lambda name: "0.2.0")

    def unexpected(version):
        pytest.fail("downgrade must not download an installer")

    monkeypatch.setattr(lifecycle, "download_installer", unexpected)
    with pytest.raises(ValueError, match="Downgrades"):
        lifecycle.update("0.1.9")
    assert managed.version() == "dogpdkb 0.1.0"


def test_cli_requires_version_and_passes_yes(monkeypatch):
    with pytest.raises(SystemExit):
        cli.main(["update"])
    monkeypatch.setattr(lifecycle, "uninstall", lambda yes: 0 if yes else 1)
    assert cli.main(["uninstall", "--yes"]) == 0
