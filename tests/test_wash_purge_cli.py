"""Wash removal contracts through the public CLI and real local files."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.source_fixture import yuque_binding

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
                    "sources": [
                        {
                            "key": "team",
                            "type": "yuque",
                            "url": f"https://example.invalid/{key}",
                        }
                    ],
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
                "schema": "dogpdkb-run-2",
                "run_id": name,
                "book": {"key": book},
                "status": status,
                "targets": {
                    "sources": yuque_binding(f"https://example.invalid/{book}"),
                    "corpus": str(tmp_path / "corpus" / book),
                    "graph": str(tmp_path / "corpus" / book / "graphify-out"),
                },
            }
        )
    )
    (directory / "failed.md").write_text("# Failed source\n![image](failed.png)\n")
    (directory / "failed.png").write_bytes(b"failure evidence")
    return directory


@contextmanager
def external_writer(lock_path):
    """A real external process owns the existing kernel lock, not its PID text."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, sys; "
                "lock = open(sys.argv[1], 'r'); "
                "fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); "
                "print('locked', flush=True); sys.stdin.read()"
            ),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        yield
    finally:
        process.stdin.close()
        process.wait(timeout=5)
        process.stdout.close()


def change_execution_targets(tmp_path, config):
    books = tmp_path / "books.json"
    rows = json.loads(books.read_text())
    rows[0]["corpus"] = "new-corpus"
    books.write_text(json.dumps(rows))
    config.write_text(
        config.read_text().replace("[paths]\n", '[paths]\ngraph_dir="new-graph"\n')
    )


@pytest.mark.parametrize("target", ["corpus", "graph"])
@pytest.mark.parametrize("selection", ["single", "all"])
def test_purge_obeys_saved_target_lock_after_parent_exits(tmp_path, target, selection):
    config = instance(tmp_path)
    selected = run_directory(tmp_path, "run-z", status="running")
    metadata = selected / "run.json"
    data = json.loads(metadata.read_text())
    dead_parent = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    data["pid"] = int(dead_parent.stdout)
    metadata.write_text(json.dumps(data))
    lock_path = Path(data["targets"][target]) / ".dogpdkb.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(data["pid"]))
    change_execution_targets(tmp_path, config)
    candidates = [selected]
    if selection == "all":
        earlier = run_directory(tmp_path, "run-a")
        earlier_data = json.loads((earlier / "run.json").read_text())
        earlier_data["targets"].update(
            corpus=str(tmp_path / "new-corpus"), graph=str(tmp_path / "new-graph")
        )
        (earlier / "run.json").write_text(json.dumps(earlier_data))
        candidates.append(earlier)
    args = ["--all"] if selection == "all" else ["--run", selected.name]
    with external_writer(lock_path):
        denied = cli(config, "--book", "funds", *args, "--force")
        assert denied.returncode == 1, denied.stdout
        assert "another writer" in denied.stderr
        assert all((path / "failed.png").is_file() for path in candidates)
        assert lock_path.read_text() == str(data["pid"])
    result = cli(config, "--book", "funds", *args, "--force")
    assert result.returncode == 0, result.stderr
    assert all(not path.exists() for path in candidates)
    assert lock_path.read_text() == str(data["pid"])


@pytest.mark.parametrize("existing_lock", [False, True])
def test_purge_holds_all_saved_target_locks_through_deletion(tmp_path, existing_lock):
    config = instance(tmp_path)
    selected = run_directory(tmp_path, "run-z")
    earlier = run_directory(tmp_path, "run-a")
    targets = json.loads((selected / "run.json").read_text())["targets"]
    lock_paths = [Path(targets[key]) / ".dogpdkb.lock" for key in ("corpus", "graph")]
    for path in lock_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing_lock:
            path.write_text("old owner")
    change_execution_targets(tmp_path, config)
    evidence = tmp_path / "deletion-locks.jsonl"
    contender = """
import fcntl, json, sys
results = []
for path in sys.argv[1:]:
    with open(path, 'r') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            results.append('held')
        else:
            results.append('free')
print(json.dumps(results))
"""
    # Observe the real deletion boundary; no product function is replaced.
    wrapper = """
import json, runpy, subprocess, sys
evidence, contender, lock_paths = sys.argv[1:4]
sys.argv = ['dogpdkb', *sys.argv[4:]]
def audit(event, args):
    if event == 'shutil.rmtree':
        result = subprocess.run(
            [sys.executable, '-c', contender, *json.loads(lock_paths)],
            capture_output=True, text=True, check=True,
        )
        with open(evidence, 'a') as stream:
            stream.write(result.stdout)
sys.addaudithook(audit)
runpy.run_module('dogpdkb', run_name='__main__')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            wrapper,
            str(evidence),
            contender,
            json.dumps([str(path) for path in lock_paths]),
            "purge",
            "--config",
            str(config),
            "--book",
            "funds",
            "--all",
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not selected.exists() and not earlier.exists()
    assert [json.loads(line) for line in evidence.read_text().splitlines()] == [
        ["held", "held"],
        ["held", "held"],
    ]
    assert all(
        path.read_text() == ("old owner" if existing_lock else "")
        for path in lock_paths
    )


@pytest.mark.parametrize("target", ["corpus", "graph"])
@pytest.mark.parametrize("invalid", [None, "", "relative/path", 5, "overlap"])
def test_purge_rejects_unsafe_saved_targets_before_any_deletion(
    tmp_path, target, invalid
):
    config = instance(tmp_path)
    earlier = run_directory(tmp_path, "run-a")
    selected = run_directory(tmp_path, "run-z")
    metadata = selected / "run.json"
    data = json.loads(metadata.read_text())
    data["targets"][target] = (
        str(earlier / "saved-data") if invalid == "overlap" else invalid
    )
    metadata.write_text(json.dumps(data))
    result = cli(config, "--book", "funds", "--all", "--force")
    assert result.returncode == 1, result.stdout
    assert "saved" in result.stderr
    assert earlier.exists() and selected.exists()


def test_purge_does_not_follow_saved_lock_symlink(tmp_path):
    config = instance(tmp_path)
    selected = run_directory(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    corpus = tmp_path / "corpus" / "funds"
    corpus.mkdir(parents=True)
    (corpus / ".dogpdkb.lock").symlink_to(outside)
    change_execution_targets(tmp_path, config)
    result = cli(config, "--book", "funds", "--run", selected.name, "--force")
    assert result.returncode == 1, result.stdout
    assert selected.exists() and outside.read_text() == "untouched"


def test_purge_does_not_recreate_missing_saved_target_directories(tmp_path):
    config = instance(tmp_path)
    selected = run_directory(tmp_path)
    change_execution_targets(tmp_path, config)
    result = cli(config, "--book", "funds", "--run", selected.name)
    assert result.returncode == 0, result.stderr
    assert not selected.exists()
    assert not (tmp_path / "corpus").exists()


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
                    "schema": "dogpdkb-state-2",
                    "book": {
                        "key": "funds",
                        "sources": yuque_binding("https://example.invalid/funds"),
                    },
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


@pytest.mark.parametrize("changed", ["corpus", "graph"])
@pytest.mark.parametrize(
    "selection,status",
    [("single", "completed"), ("single", "paused"), ("all", "completed")],
)
def test_purge_owned_wash_after_execution_target_changes(
    tmp_path, changed, selection, status
):
    config = instance(tmp_path)
    selected = run_directory(tmp_path, status=status)
    if changed == "corpus":
        books = tmp_path / "books.json"
        rows = json.loads(books.read_text())
        rows[0]["corpus"] = "new-corpus"
        books.write_text(json.dumps(rows))
    else:
        config.write_text(
            config.read_text().replace("[paths]\n", '[paths]\ngraph_dir="new-graph"\n')
        )
    preserved = []
    for name in (
        "corpus/funds/source.md",
        "corpus/funds/graphify-out/graph.json",
        "new-corpus/source.md",
        "new-graph/graph.json",
        "state/funds/inputs/raw.md",
        "cache/image.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        preserved.append(path)
    args = ["--all"] if selection == "all" else ["--run", selected.name]
    runs = [selected]
    if selection == "all":
        current = run_directory(tmp_path, "run-current")
        data = json.loads((current / "run.json").read_text())
        data["targets"][changed] = str(
            tmp_path / ("new-corpus" if changed == "corpus" else "new-graph")
        )
        (current / "run.json").write_text(json.dumps(data))
        runs.append(current)
    if status == "paused":
        denied = cli(config, "--book", "funds", *args)
        assert denied.returncode == 1 and "--force" in denied.stderr
        assert selected.exists()
        args.append("--force")
    result = cli(config, "--book", "funds", *args)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)["purged"]) == {str(run) for run in runs}
    assert all(not run.exists() for run in runs)
    assert all(
        path.read_text() == str(path.relative_to(tmp_path)) for path in preserved
    )


@pytest.mark.parametrize("selection", ["single", "all"])
def test_purge_owns_old_scope_after_subscription_changes(tmp_path, selection):
    config = instance(tmp_path)
    run = run_directory(tmp_path, status="paused")
    saved = run / "run.json"
    data = json.loads(saved.read_text())
    data["targets"]["sources"]["team"]["document_ids"] = ["42"]
    saved.write_text(json.dumps(data))
    books = tmp_path / "books.json"
    rows = json.loads(books.read_text())
    rows[0]["sources"][0]["exclude_document_ids"] = ["43"]
    books.write_text(json.dumps(rows))
    artifact = tmp_path / "state/funds/inputs/source.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Preserve durable input\n")
    args = ["--all"] if selection == "all" else ["--run", run.name]
    result = cli(config, "--book", "funds", *args, "--force")
    assert result.returncode == 0, result.stderr
    assert not run.exists()
    assert artifact.read_text() == "# Preserve durable input\n"


@pytest.mark.parametrize("invalid", ["schema", "book", "source", "run-id"])
def test_purge_all_rejects_invalid_saved_ownership_before_deleting(tmp_path, invalid):
    config = instance(tmp_path)
    valid = run_directory(tmp_path, "run-a")
    invalid_run = run_directory(tmp_path, "run-z")
    metadata = invalid_run / "run.json"
    data = json.loads(metadata.read_text())
    if invalid == "schema":
        data["schema"] = "unknown"
    elif invalid == "book":
        data["book"]["key"] = "other"
    elif invalid == "source":
        data["targets"]["sources"]["team"]["url"] = "https://example.invalid/other"
    else:
        data["run_id"] = "run-elsewhere"
    metadata.write_text(json.dumps(data))
    result = cli(config, "--book", "funds", "--all", "--force")
    assert result.returncode == 1, result.stdout
    assert valid.exists() and invalid_run.exists()


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
