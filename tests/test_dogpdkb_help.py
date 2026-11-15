"""Help contracts through the public executable boundary, without live tools."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def invoke(tmp_path, *args):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DOGPDKB_CONFIG": str(tmp_path / "missing.toml"),
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_sync_full_help_is_a_local_execution_contract(tmp_path):
    result = invoke(tmp_path, "sync", "--help", "--full")
    assert result.returncode == 0, result.stderr
    for text in (
        "Configuration",
        "DOGPDKB_CONFIG",
        "--stop-after plan",
        "Exit codes",
        "dogpdkb-run-1",
        "schema",
        "partial",
        "suppression",
        "stdout",
        "stderr",
    ):
        assert text in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["sync", "update-graph"])
def test_graphify_input_mode_is_documented_locally(tmp_path, command):
    result = invoke(tmp_path, command, "--help", "--full")
    assert result.returncode == 0, result.stderr
    assert "--graphify-inputs {all,text}" in result.stdout
    assert "Markdown" in result.stdout
    assert "default" in result.stdout
    assert not list(tmp_path.iterdir())


COMMANDS = (
    "",
    "discover",
    "apply",
    "update-graph",
    "sync",
    "status",
    "purge",
    "doctor",
    "init-config",
    "init-state",
    "publish-web",
    "update",
    "uninstall",
)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_scope_has_equivalent_short_long_and_full_help(tmp_path, command):
    scope = [command] if command else []
    ordinary = invoke(tmp_path, *scope, "--help")
    short = invoke(tmp_path, *scope, "-h")
    full = invoke(tmp_path, *scope, "--help", "--full")
    short_full = invoke(tmp_path, *scope, "-h", "--full")
    for result in (ordinary, short, full, short_full):
        assert result.returncode == 0, result.stderr
        assert not result.stderr
        assert "Local contract:" in result.stdout
    assert ordinary.stdout == short.stdout
    assert full.stdout == short_full.stdout
    assert len(full.stdout) > len(ordinary.stdout)
    assert f"usage: dogpdkb{' ' + command if command else ''}" in full.stdout
    if command:
        assert "Example:" in ordinary.stdout
        assert "stdout" in ordinary.stdout
        assert "stderr" in ordinary.stdout
        assert "Exit codes:" in " ".join(ordinary.stdout.split())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", COMMANDS)
def test_standalone_full_is_usage_error_before_work(tmp_path, command):
    result = invoke(tmp_path, *([command] if command else []), "--full")
    assert result.returncode in {2, 64}, result.stderr
    assert "--full requires -h or --help" in result.stderr
    assert "--help" in result.stderr
    assert not result.stdout
    assert not list(tmp_path.iterdir())


def test_update_target_version_does_not_become_global_version(tmp_path):
    result = invoke(tmp_path, "update", "--version", "0.2.0", "--help")
    assert result.returncode == 0
    assert "MAJOR.MINOR.PATCH" in result.stdout
    assert "usage: dogpdkb update" in result.stdout


def test_full_before_command_is_rejected_with_correct_placement(tmp_path):
    result = invoke(tmp_path, "--full", "sync", "--help")
    assert result.returncode == 64
    assert not result.stdout
    assert "dogpdkb sync --help --full" in result.stderr
    assert "after the command" in result.stderr
    assert not list(tmp_path.iterdir())


def test_abbreviated_full_cannot_create_configuration(tmp_path):
    result = invoke(tmp_path, "init-config", "--ful", "--output", "out")
    assert result.returncode == 64
    assert "unrecognized arguments: --ful" in result.stderr
    assert not result.stdout
    assert not list(tmp_path.iterdir())
