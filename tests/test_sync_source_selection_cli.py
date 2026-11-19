"""Direct input and selected preflight through the public dogpdkb CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_install import REMOTE_CLI

ROOT = Path(__file__).resolve().parents[1]
YUQUE = {"key": "team", "type": "yuque", "url": "https://example.invalid/book"}


def cli(cwd, *args):
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *map(str, args)],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def instance(root, sources, *, second_book=False):
    rows = [{"key": "funds", "sources": sources}]
    if second_book:
        rows.append({"key": "second", "sources": [YUQUE]})
    (root / "books.json").write_text(json.dumps(rows))
    config = root / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[tools]\n'
        + f"mdcompare={json.dumps([sys.executable, '-m', 'mdcompare'])}\n"
        + 'skylark="unexpected-skylark-executable"\n'
    )
    return config


def run_data(result):
    assert result.returncode == 0, result.stderr
    path = Path(json.loads(result.stdout)["run"])
    return path, json.loads(path.read_text())


def test_sync_direct_root_is_frozen_and_uses_caller_cwd(tmp_path):
    config = instance(tmp_path, [YUQUE])
    tools = tmp_path / "local-tools.py"
    tools.write_text(REMOTE_CLI)
    with config.open("a") as stream:
        for name in ("graphify", "mdocr"):
            stream.write(f"{name}={json.dumps([sys.executable, str(tools), name])}\n")
    caller = tmp_path / "caller"
    source = caller / "2026-11-19"
    source.mkdir(parents=True)
    note = source / "note.md"
    original = b"# Local notes\r\n\r\nCheck the funding limit.\r\n"
    note.write_bytes(original)
    before = (tmp_path / "books.json").read_bytes()

    path, run = run_data(
        cli(
            caller,
            "sync",
            "--config",
            config,
            "--dir",
            source.name,
            "--stop-after",
            "plan",
        )
    )
    [key] = run["sources"]
    assert run["targets"]["sources"] == {
        key: {"type": "directory", "path": str(source)}
    }
    plan = json.loads(Path(run["artifacts"]["plan"]).read_text())
    [row] = plan["documents"]
    assert row["source_document_id"] == "note.md"
    assert row["source_key"] == key
    assert Path(row["input_copy"]).read_bytes() == original
    assert (tmp_path / "books.json").read_bytes() == before

    source.rename(caller / "moved")
    _, resumed = run_data(
        cli(
            tmp_path,
            "sync",
            "--config",
            config,
            "--resume",
            path,
            "--stop-after",
            "plan",
        )
    )
    assert resumed["targets"] == run["targets"]
    assert resumed["artifacts"]["plan"] == run["artifacts"]["plan"]
    assert Path(row["input_copy"]).read_bytes() == original

    _, completed = run_data(cli(tmp_path, "sync", "--config", config, "--resume", path))
    assert completed["status"] == "completed"
    corpus = tmp_path / "corpus" / "funds"
    [published] = corpus.glob("*.md")
    assert published.read_text() == "# Local notes\n\nCheck the funding limit.\n"
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    record = state["documents"][row["document_id"]]
    assert record["source_key"] == key
    assert Path(record["raw"]["path"]).read_bytes() == original
    assert (corpus / "graphify-out" / "graph.json").is_file()
    calls = [
        json.loads(line)[:2]
        for line in (tmp_path / "commands.jsonl").read_text().splitlines()
    ]
    assert [call for call in calls if call[0] == "graphify"] == [
        ["graphify", "extract"]
    ]
    assert (tmp_path / "books.json").read_bytes() == before


def test_doctor_checks_only_selected_sources_and_local_help(tmp_path):
    local = tmp_path / "inbox"
    local.mkdir()
    config = instance(
        tmp_path,
        [
            YUQUE,
            {"key": "inbox", "type": "directory", "path": "inbox"},
            {"key": "broken", "type": "directory", "path": "missing"},
        ],
    )
    fake = tmp_path / "tool.py"
    fake.write_text(
        "import sys\nfrom pathlib import Path\n"
        "assert sys.argv[2:] == ['--help']\n"
        "with Path(__file__).with_name('events').open('a') as f:\n"
        "    print(sys.argv[1], file=f)\n"
    )
    with config.open("a") as stream:
        for name in ("graphify", "mdocr"):
            stream.write(f"{name}={json.dumps([sys.executable, str(fake), name])}\n")

    for selector in (("--source", "inbox"), ("--dir", local)):
        result = cli(tmp_path, "doctor", "--config", config, *selector, "--json")
        assert result.returncode == 0, result.stderr or result.stdout
        checks = json.loads(result.stdout)
        assert {row["name"] for row in checks} >= {"graphify", "mdcompare", "mdocr"}
        assert not {"skylark", "broken"} & {row["name"] for row in checks}
    assert (tmp_path / "events").read_text().splitlines() == ["graphify", "mdocr"] * 2
    (tmp_path / "events").unlink()

    invalid = cli(
        tmp_path, "doctor", "--config", config, "--source", "broken", "--json"
    )
    assert invalid.returncode == 1, invalid.stderr
    [failed] = [row for row in json.loads(invalid.stdout) if row["status"] == "fail"]
    assert "missing" in failed["reason"]
    assert not (tmp_path / "events").exists()
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "corpus").exists()


def test_direct_root_reuses_registered_identity_but_not_another_roots(tmp_path):
    inbox, other = tmp_path / "inbox", tmp_path / "other"
    (inbox / "assets").mkdir(parents=True)
    other.mkdir()
    (inbox / "assets" / "sample.png").write_bytes(b"image bytes")
    (inbox / "note.md").write_text(
        "# Inbox\n\n![image](assets/sample.png) ![missing](gone.png)\n"
    )
    (inbox / "_inventory").mkdir()
    (inbox / "_inventory" / "ignored.md").write_text("# Ignore\n")
    (other / "note.md").write_text("# Independent note\n")
    config = instance(
        tmp_path, [YUQUE, {"key": "inbox", "type": "directory", "path": "inbox"}]
    )

    def selected(*flags):
        _, run = run_data(
            cli(tmp_path, "sync", "--config", config, *flags, "--stop-after", "plan")
        )
        differences = json.loads(Path(run["artifacts"]["differences"]).read_text())
        assert not differences["absent"]
        [row] = differences["documents"]
        return row

    first = selected("--source", "inbox")
    assert first["source_key"] == "inbox"
    frozen = Path(first["frozen_input"]).parent
    assert (frozen / "assets" / "sample.png").read_bytes() == b"image bytes"
    assert not (frozen / "gone.png").exists()
    alias = tmp_path / "alias"
    alias.symlink_to(inbox, target_is_directory=True)
    matching = selected("--dir", alias)
    assert matching["document_id"] == first["document_id"]
    state = tmp_path / "state" / "funds" / "state.json"
    inbox_observation = json.loads(state.read_text())["observation"]["inbox"]
    (inbox / "note.md").unlink()

    separate = selected("--dir", other)
    assert separate["source_document_id"] == first["source_document_id"]
    assert separate["document_id"] != first["document_id"]
    saved = json.loads(state.read_text())
    assert saved["observation"]["inbox"] == inbox_observation
    assert "absence" not in saved["documents"][first["document_id"]]
    assert "team" not in saved["observation"]


def test_sync_directory_requires_a_unique_target_and_rejects_mixed_input(tmp_path):
    source = tmp_path / "second"  # The directory name cannot infer the book.
    source.mkdir()
    config = instance(tmp_path, [YUQUE], second_book=True)
    for selectors, code, message in (
        (("--dir", source), 1, "--book"),
        (("--dir", source, "--source", "team"), 64, "--source"),
        (("--resume", "run-unused", "--dir", source), 64, "--resume"),
        (("--resume", "run-unused", "--source", "team"), 64, "--resume"),
        (("--dir", tmp_path, "--book", "funds"), 1, "overlaps a managed path"),
    ):
        result = cli(tmp_path, "sync", "--config", config, *selectors)
        assert result.returncode == code, result.stderr
        assert message in result.stderr
    for path in ("runs", "state", "corpus"):
        assert not (tmp_path / path).exists()
    _, run = run_data(
        cli(
            tmp_path,
            "sync",
            "--config",
            config,
            "--book",
            "funds",
            "--dir",
            source,
            "--stop-after",
            "plan",
        )
    )
    assert run["book"]["key"] == "funds"


def test_failed_direct_scan_leaves_no_observation_and_can_be_purged(tmp_path):
    config = instance(tmp_path, [YUQUE])
    source = tmp_path / "inbox"
    source.mkdir()
    (tmp_path / "external.png").write_bytes(b"outside the input root")
    note = source / "note.md"
    note.write_text("# Inbox\n\n![image](../external.png)\n")
    result = cli(
        tmp_path, "sync", "--config", config, "--dir", source, "--stop-after", "plan"
    )
    assert result.returncode == 1, result.stderr
    assert "outside its directory source" in result.stderr
    [run_path] = (tmp_path / "runs").rglob("run.json")
    assert not (run_path.parent / "differences.json").exists()
    state = tmp_path / "state" / "funds" / "state.json"
    if state.exists():
        assert not json.loads(state.read_text())["observation"]
    purged = cli(tmp_path, "purge", "--config", config, "--run", run_path, "--force")
    assert purged.returncode == 0, purged.stderr
    assert not run_path.exists()
    assert note.exists()
