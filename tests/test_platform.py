"""Offline platform CLI, safe initialization, and read-only preflight contracts."""

import json
import os
import sys
from unittest.mock import Mock

import pytest

from dogpdkb import cli, doctor
from mdocr import config as settings


@pytest.fixture(autouse=True)
def platform_environment(monkeypatch):
    for name in os.environ:
        if name.startswith(("DOGPDKB_", "SKYLARK_", "UTOO_", "GRAPHIFY_")):
            monkeypatch.delenv(name)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / "dogpdkb.toml"
    path.write_text(
        '[source]\nbooks_file = "books.json"\n'
        '[paths]\ncorpus = "corpus"\nstaging = "new/staging"\n'
        'state = "new/state"\nweb = "new/web"\n'
        '[skylark]\nmcp_url = "https://example.com/mcp"\n'
        f'proxy_bin = "{tmp_path}/proxy"\n'
    )
    (tmp_path / "books.json").write_text(
        json.dumps([{"key": "one", "url": "https://example.com/book"}])
    )
    graph_dir = tmp_path / "corpus/one/graphify-out"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.json").write_text('{"nodes": [], "links": []}')
    proxy = tmp_path / "proxy"
    proxy.write_text("#!/bin/sh\nexit 99\n")
    proxy.chmod(0o755)
    monkeypatch.setattr(doctor, "graph_runtime", Mock(return_value=(True, True)))
    return path


@pytest.mark.parametrize("command", cli.WORKFLOWS)
def test_workflow_forwards_arguments_and_exit_status(command, monkeypatch):
    call = Mock(return_value=17)
    monkeypatch.setattr(cli.subprocess, "call", call)
    arguments = ["--config", "custom.toml", "--book", "one", "--help"]
    assert cli.main([command, *arguments]) == 17
    script = cli.RESOURCES.parent / "workflows" / cli.WORKFLOWS[command]
    call.assert_called_once_with([sys.executable, str(script), *arguments])


def test_help_and_version(capsys):
    assert cli.main([]) == 0
    assert "doctor" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        cli.main(["--version"])
    assert error.value.code == 0
    assert capsys.readouterr().out.startswith("dogpdkb ")


def test_init_config_is_disabled_and_preserves_existing_files(tmp_path):
    assert cli.main(["init-config", "-o", str(tmp_path)]) == 0
    config = settings.load_config(tmp_path / "dogpdkb.toml")
    books = json.loads(config.books_file.read_text())
    assert books and all(book["enabled"] is False for book in books)
    assert ".invalid" in config.mcp_url
    with pytest.raises(settings.ConfigError, match="exactly one"):
        settings.select_book(config)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError):
        cli.init_config(tmp_path)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("name", ["dogpdkb.toml", "books.json"])
@pytest.mark.parametrize("symlink", [False, True])
def test_init_config_refuses_partial_or_dangling_symlink(tmp_path, name, symlink):
    path = tmp_path / name
    if symlink:
        path.symlink_to(tmp_path / "missing-target")
    else:
        path.write_text("keep")
    with pytest.raises(FileExistsError):
        cli.init_config(tmp_path)
    assert list(tmp_path.iterdir()) == [path]
    assert path.is_symlink() if symlink else path.read_text() == "keep"


def test_package_doctor_needs_no_config_graphify_or_token(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("package doctor must not inspect operational dependencies")

    monkeypatch.setattr(doctor, "load_config", unexpected)
    monkeypatch.setattr(doctor, "graph_runtime", unexpected)
    assert cli.main(["doctor", "--package", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.parametrize("runtime", [(True, True), (True, False), (False, False)])
def test_operational_doctor_is_read_only(configured, monkeypatch, capsys, runtime):
    monkeypatch.setattr(doctor, "graph_runtime", Mock(return_value=runtime))
    monkeypatch.setenv("PADDLEOCR_TOKEN", "secret-never-print")
    root = configured.parent
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    assert cli.main(["doctor", "--config", str(configured), "--json"]) == (
        0 if all(runtime) else 1
    )
    output = capsys.readouterr().out
    assert "secret-never-print" not in output
    report = json.loads(output)
    statuses = {row["check"]: row["status"] for row in report["checks"]}
    assert statuses["graphify-api"] == ("pass" if runtime[0] else "fail")
    if runtime[0]:
        assert statuses["graphify-backend"] == ("pass" if runtime[1] else "fail")
    assert report["ok"] is all(runtime)
    assert not (root / "new").exists()
    assert before == {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("target", ["config", "graph"])
@pytest.mark.parametrize(
    "contents", [None, "broken", '{"nodes": {}}', '[skylark]\nmcp_url = "https://["']
)
def test_missing_or_corrupt_inputs_fail(configured, target, contents):
    path = (
        configured
        if target == "config"
        else configured.parent / "corpus/one/graphify-out/graph.json"
    )
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    checks = {row["check"]: row["status"] for row in doctor.diagnose(configured)}
    assert checks["configuration" if target == "config" else "graph-baseline"] == "fail"


@pytest.mark.parametrize("package_only", [False, True])
@pytest.mark.parametrize("require_ocr", [False, True])
def test_ocr_token_requirement(configured, package_only, require_ocr):
    checks = doctor.diagnose(
        configured, package_only=package_only, require_ocr=require_ocr
    )
    statuses = {row["check"]: row["status"] for row in checks}
    assert ("ocr-token" in statuses) is (require_ocr or not package_only)
    if "ocr-token" in statuses:
        assert statuses["ocr-token"] == ("fail" if require_ocr else "warn")
    assert any(status == "fail" for status in statuses.values()) is require_ocr


def test_missing_runtime_dependency_is_reported(monkeypatch):
    original = doctor.importlib.import_module

    def missing(name):
        if name == "requests":
            raise ImportError("secret dependency details")
        return original(name)

    monkeypatch.setattr(doctor.importlib, "import_module", missing)
    checks = doctor.diagnose(package_only=True)
    assert next(row for row in checks if row["check"] == "requests")["status"] == "fail"
    assert "secret dependency details" not in json.dumps(checks)


def test_installed_default_config_and_precedence(configured, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DEFAULT_CONFIG", tmp_path / "absent-default.toml")
    monkeypatch.chdir(tmp_path)
    assert settings.load_config().file == configured
    explicit = tmp_path / "explicit.toml"
    explicit.write_bytes(configured.read_bytes())
    monkeypatch.setenv("DOGPDKB_CONFIG", str(explicit))
    assert settings.load_config().file == explicit
    assert settings.load_config(configured).file == configured
