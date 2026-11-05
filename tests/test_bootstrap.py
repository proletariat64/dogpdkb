"""Regression checks for sharing dependencies across worktree environments."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/bootstrap.py"
SPEC = importlib.util.spec_from_file_location("bootstrap", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


@pytest.fixture
def environments(tmp_path, monkeypatch):
    source = tmp_path / "main/.venv"
    target = tmp_path / "worktree/.venv"
    (source / "bin").mkdir(parents=True)
    (target / "bin").mkdir(parents=True)
    (target / "bin/python").symlink_to(sys.executable)
    monkeypatch.setattr(bootstrap.sys, "prefix", str(source))
    lock = tmp_path / "uv.lock"
    lock.write_text("""
[[package]]
name = "example"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "mdocr"
version = "1.0"
source = { editable = "." }
""")
    return source, target, lock


def distribution(name, version, files):
    return SimpleNamespace(
        metadata={"Name": name},
        version=version,
        files=list(files),
        locate_file=lambda record: files[record],
    )


def test_seed_shares_files_and_rebinds_python_entrypoints(environments, monkeypatch):
    source, target, lock = environments
    module = source / "lib/example.py"
    module.parent.mkdir()
    module.write_text("value = 1\n")
    command = source / "bin/example"
    command.write_text(f"#!{source}/bin/python\nprint('example')\n")
    command.chmod(0o755)
    package = distribution("example", "1.0", {"module": module, "script": command})
    monkeypatch.setattr(
        bootstrap.importlib.metadata, "distributions", lambda: [package]
    )

    bootstrap.seed_from_main(target, lock)
    assert module.samefile(target / "lib/example.py")
    assert (target / "bin/example").read_text().startswith(f"#!{target}/bin/python\n")
    assert (target / "bin/example").stat().st_mode & 0o111
    bootstrap.seed_from_main(target, lock)
    assert module.samefile(target / "lib/example.py")


def test_seed_excludes_wrong_versions_editables_and_external_files(
    environments, monkeypatch, tmp_path
):
    source, target, lock = environments
    outside = tmp_path / "outside.py"
    outside.write_text("private = True\n")
    local = source / "lib/module.py"
    local.parent.mkdir()
    local.write_text("value = 1\n")
    packages = [
        distribution("example", "2.0", {"wrong": local}),
        distribution("mdocr", "1.0", {"editable": local}),
        distribution("example", "1.0", {"external": outside}),
    ]
    monkeypatch.setattr(bootstrap.importlib.metadata, "distributions", lambda: packages)
    bootstrap.seed_from_main(target, lock)
    assert not (target / "lib").exists()
    assert not (target / "outside.py").exists()


def test_seed_skips_incompatible_python(environments, monkeypatch):
    _, target, lock = environments
    monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *a, **k: "other\n")
    monkeypatch.setattr(
        bootstrap.importlib.metadata,
        "distributions",
        lambda: pytest.fail("Must not inspect packages from incompatible Python"),
    )
    bootstrap.seed_from_main(target, lock)
    assert not (target / "lib").exists()


@pytest.mark.parametrize("source_shim", [False, True])
def test_seeded_launchers_execute_in_paths_with_spaces(
    environments, monkeypatch, source_shim
):
    import shlex
    import subprocess

    source, original_target, lock = environments
    target = original_target.parent.parent / "work tree's env/.venv"
    (target / "bin").mkdir(parents=True)
    (target / "bin/python").symlink_to(sys.executable)
    command = source / "bin/example"
    body = "import sys;print(sys.executable)\n"
    header = (
        "#!/bin/sh\n"
        + "'''exec' "
        + shlex.quote(str(source / "bin/python"))
        + ' "$0" "$@"\n'
        + "' '''\n"
        if source_shim
        else f"#!{source}/bin/python\n"
    )
    command.write_text(header + body)
    command.chmod(0o755)
    monkeypatch.setattr(
        bootstrap.importlib.metadata,
        "distributions",
        lambda: [
            distribution("example", "1.0", {"script": command}),
        ],
    )
    bootstrap.seed_from_main(target, lock)
    completed = subprocess.run(
        [str(target / "bin/example")], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == str(target / "bin/python")
    assert not command.samefile(target / "bin/example")


def test_seed_skips_whole_distribution_with_unknown_launcher(environments, monkeypatch):
    source, target, lock = environments
    metadata = source / "lib/example-1.0.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Name: example\nVersion: 1.0\n")
    command = source / "bin/example"
    command.write_text('#!/bin/sh\nexec /some/main/python "$@"\n')
    command.chmod(0o755)
    monkeypatch.setattr(
        bootstrap.importlib.metadata,
        "distributions",
        lambda: [
            distribution("example", "1.0", {"metadata": metadata, "script": command}),
        ],
    )
    bootstrap.seed_from_main(target, lock)
    assert not (target / "lib/example-1.0.dist-info").exists()
    assert not (target / "bin/example").exists()
