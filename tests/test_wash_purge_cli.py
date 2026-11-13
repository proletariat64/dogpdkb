"""Wash removal contracts through the public CLI and real local files."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def cli(config, *args):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "purge",
            "--config",
            str(config),
            *map(str, args),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def instance(tmp_path):
    config = tmp_path / "dogpdkb.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstaging="wash"\nstate="state"\nocr_cache="cache"\n'
    )
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": key,
                    "url": f"https://example.invalid/{key}",
                    "corpus": f"corpus/{key}",
                }
                for key in ("funds", "other")
            ]
        )
    )
    return config


def run_directory(tmp_path, name="run-example", status="completed", book="funds"):
    directory = tmp_path / "wash" / book / name
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(
        json.dumps(
            {
                "schema": "dogpdkb-run-1",
                "run_id": name,
                "book": {"key": book},
                "status": status,
                "targets": {
                    "book_url": f"https://example.invalid/{book}",
                    "corpus": str(tmp_path / "corpus" / book),
                    "graph": str(tmp_path / "corpus" / book / "graphify-out"),
                },
            }
        )
    )
    (directory / "failed.md").write_text("# Failed source\n![image](failed.png)\n")
    (directory / "failed.png").write_bytes(b"failure evidence")
    return directory


def test_purge_removes_selected_wash_and_preserves_other_data(tmp_path):
    config = instance(tmp_path)
    selected = run_directory(tmp_path)
    other = run_directory(tmp_path, book="other")
    preserved = []
    for name in (
        "state/funds/state.json",
        "state/funds/inputs/raw.md",
        "corpus/funds/source.md",
        "corpus/funds/graphify-out/graph.json",
        "cache/image.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": "dogpdkb-state-1",
                    "book": {"key": "funds", "url": "https://example.invalid/funds"},
                    "documents": {},
                }
            )
            if name.endswith("state.json")
            else "retained"
        )
        preserved.append(path)
    before = {path: path.read_bytes() for path in preserved}
    result = cli(config, "--book", "funds", "--run", selected.name)
    assert result.returncode == 0, result.stderr
    assert not selected.exists()
    assert other.is_dir()
    assert all(path.read_bytes() == before[path] for path in preserved)


@pytest.mark.parametrize("status", ["paused", "failed", "interrupted", "running"])
def test_purge_requires_force_for_unfinished_run(tmp_path, status):
    config = instance(tmp_path)
    directory = run_directory(tmp_path, status=status)
    denied = cli(config, "--book", "funds", "--run", directory.name)
    assert denied.returncode == 1 and "--force" in denied.stderr
    assert (directory / "failed.md").is_file()
    result = cli(config, "--book", "funds", "--run", directory, "--force")
    assert result.returncode == 0, result.stderr
    assert not directory.exists()


def test_purge_all_validates_every_run_before_deleting(tmp_path):
    config = instance(tmp_path)
    completed = run_directory(tmp_path, "run-a")
    paused = run_directory(tmp_path, "run-z", "paused")
    other = run_directory(tmp_path, book="other")
    denied = cli(config, "--book", "funds", "--all")
    assert denied.returncode == 1
    assert completed.exists() and paused.exists()
    result = cli(config, "--book", "funds", "--all", "--force")
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["purged"]) == 2
    assert not completed.exists() and not paused.exists()
    assert other.exists()


@pytest.mark.parametrize("args", [[], ["--run", "run-x", "--all"]])
def test_purge_requires_exactly_one_target(tmp_path, args):
    result = cli(instance(tmp_path), "--book", "funds", *args)
    assert result.returncode == 64
    assert not (tmp_path / "wash").exists()


def test_purge_force_still_obeys_writer_lock(tmp_path):
    config = instance(tmp_path)
    directory = run_directory(tmp_path)
    corpus = tmp_path / "corpus" / "funds"
    corpus.mkdir(parents=True)
    with (corpus / ".dogpdkb.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = cli(config, "--book", "funds", "--run", directory.name, "--force")
    assert result.returncode == 1 and "another writer" in result.stderr
    assert (directory / "failed.png").read_bytes() == b"failure evidence"


@pytest.mark.parametrize("reference", ["../other/run-example", "absolute", "symlink"])
def test_purge_refuses_escape_or_symlink_target(tmp_path, reference):
    config = instance(tmp_path)
    outside = run_directory(tmp_path, book="other")
    if reference == "absolute":
        reference = str(outside)
    elif reference == "symlink":
        (tmp_path / "wash" / "funds").mkdir()
        (tmp_path / "wash" / "funds" / "run-link").symlink_to(
            outside, target_is_directory=True
        )
        reference = "run-link"
    result = cli(config, "--book", "funds", "--run", reference, "--force")
    assert result.returncode == 1
    assert (outside / "failed.md").is_file()


def test_purge_refuses_symlink_book_wash(tmp_path):
    config = instance(tmp_path)
    outside = run_directory(tmp_path, book="other")
    (tmp_path / "wash" / "funds").symlink_to(outside.parent, target_is_directory=True)
    result = cli(config, "--book", "funds", "--all", "--force")
    assert result.returncode == 1 and "symlink wash" in result.stderr
    assert outside.exists()


def test_purge_does_not_follow_contained_image_symlink(tmp_path):
    config = instance(tmp_path)
    directory = run_directory(tmp_path, status="partial")
    outside = tmp_path / "retained-picture.png"
    outside.write_bytes(b"published")
    (directory / "linked.png").symlink_to(outside)
    result = cli(config, "--book", "funds", "--run", directory / "run.json")
    assert result.returncode == 0, result.stderr
    assert not directory.exists()
    assert outside.read_bytes() == b"published"


def test_force_does_not_discard_a_live_run_even_without_writer_lock(tmp_path):
    config = instance(tmp_path)
    directory = run_directory(tmp_path, status="running")
    metadata = directory / "run.json"
    data = json.loads(metadata.read_text())
    data["pid"] = os.getpid()
    metadata.write_text(json.dumps(data))
    result = cli(config, "--book", "funds", "--run", directory.name, "--force")
    assert result.returncode == 1 and "still running" in result.stderr
    assert (directory / "failed.md").is_file()


def test_purge_rejects_configured_overlap_with_durable_inputs(tmp_path):
    config = instance(tmp_path)
    directory = run_directory(tmp_path)
    config.write_text(
        config.read_text().replace(
            'state="state"', 'state="wash/funds/run-example/inputs"'
        )
    )
    retained = directory / "inputs" / "funds" / "input.md"
    retained.parent.mkdir(parents=True)
    retained.write_text("durable raw Markdown")
    result = cli(config, "--book", "funds", "--run", directory.name, "--force")
    assert result.returncode == 1 and "protected data" in result.stderr
    assert retained.read_text() == "durable raw Markdown"


def test_purge_reports_deletion_error_as_nonzero(tmp_path):
    config = instance(tmp_path)
    directory = run_directory(tmp_path)
    directory.chmod(0o555)
    try:
        result = cli(config, "--book", "funds", "--run", directory.name)
    finally:
        directory.chmod(0o755)
    assert result.returncode == 1
    assert "dogpdkb purge:" in result.stderr


@pytest.mark.parametrize("metadata", [False, True])
def test_purge_accepts_working_directory_relative_run_path(
    tmp_path, monkeypatch, metadata
):
    config = instance(tmp_path)
    selected = run_directory(tmp_path)
    monkeypatch.chdir(tmp_path)
    reference = selected.relative_to(tmp_path)
    if metadata:
        reference /= "run.json"
    result = cli(config, "--book", "funds", "--run", reference)
    assert result.returncode == 0, result.stderr
    assert not selected.exists()
