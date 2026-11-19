"""Subscription configuration through the public CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

from scripts.smoke_install import REMOTE_CLI


def cli(tmp_path, row, command="status", *flags, remote=False):
    (tmp_path / "books.json").write_text(json.dumps([row]), encoding="utf-8")
    config = tmp_path / "instance.toml"
    if remote:
        fixture = tmp_path / "remote.py"
        fixture.write_text(REMOTE_CLI, encoding="utf-8")
        commands = "\n".join(
            f"{name}={json.dumps([sys.executable, str(fixture), name])}"
            for name in ("skylark", "graphify", "mdcompare", "mdocr")
        )
    else:
        commands = (
            "mdcompare="
            + json.dumps([sys.executable, "-m", "mdcompare"])
            + '\nskylark="unexpected-skylark-executable"'
        )
    config.write_text(
        '[source]\nbooks_file="books.json"\n[tools]\n' + commands + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", command, "--config", str(config), *flags],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def status(tmp_path, row):
    return cli(tmp_path, row, "status", "--json")


def test_subscription_requires_sources_instead_of_legacy_url(tmp_path):
    result = status(
        tmp_path, {"key": "funds", "url": "https://example.invalid/team/book"}
    )
    assert result.returncode != 0
    assert "sources" in result.stderr
    assert not (tmp_path / "state").exists()


def test_status_accepts_mixed_source_configuration(tmp_path):
    result = status(
        tmp_path,
        {
            "key": "funds",
            "sources": [
                {
                    "key": "team",
                    "type": "yuque",
                    "url": "https://example.invalid/team/book",
                },
                {"key": "inbox", "type": "directory", "path": "./inbox"},
            ],
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "no-runs"


def test_status_rejects_duplicate_source_key(tmp_path):
    result = status(
        tmp_path,
        {
            "key": "funds",
            "sources": [
                {
                    "key": "team",
                    "type": "yuque",
                    "url": "https://example.invalid/team/book",
                },
                {"key": "team", "type": "directory", "path": "./inbox"},
            ],
        },
    )
    assert result.returncode != 0
    assert "duplicate source key: team" in result.stderr


def test_init_state_binds_existing_corpus_to_its_yuque_source(tmp_path):
    corpus = tmp_path / "published"
    corpus.mkdir()
    (corpus / "Balances-2026-11-18-[42].md").write_text("# Balances\n")
    subscription = {
        "key": "funds",
        "corpus": "published",
        "sources": [
            {
                "key": "team",
                "type": "yuque",
                "url": "https://example.invalid/team/book",
            },
            {"key": "inbox", "type": "directory", "path": "./inbox"},
        ],
    }
    result = cli(tmp_path, subscription, "init-state")
    assert result.returncode == 0, result.stderr
    state = json.loads(Path(json.loads(result.stdout)["state"]).read_text())
    [(identity, document)] = state["documents"].items()
    assert identity != "42"
    assert document["source_key"] == "team"
    assert document["source_document_id"] == "42"
    assert document["graph"] == {"status": "unknown"}
    assert "raw" not in document


def test_discover_only_scans_selected_directory_and_freezes_markdown(tmp_path):
    inbox = tmp_path / "inbox"
    for relative in (
        "2026-11-19/Notes.MD",
        "_inventory/ignored.md",
        ".hidden/hidden.md",
        "readme.txt",
    ):
        path = inbox / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Frozen notes\n")
    subscription = {
        "key": "funds",
        "sources": [
            {
                "key": "team",
                "type": "yuque",
                "url": "https://example.invalid/team/book",
            },
            {"key": "inbox", "type": "directory", "path": "./inbox"},
        ],
    }
    result = cli(tmp_path, subscription, "discover", "--source", "inbox")
    assert result.returncode == 0, result.stderr
    differences = json.loads(Path(json.loads(result.stdout)["differences"]).read_text())
    assert differences["sources"] == ["inbox"]
    assert not differences["absent"]
    [notes] = differences["documents"]
    assert notes["source_key"] == "inbox"
    assert notes["source_document_id"] == "2026-11-19/Notes.MD"
    assert Path(notes["frozen_input"]).read_text() == "# Frozen notes\n"


def test_sync_freezes_directory_only_plan_and_rejects_mutated_snapshot(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    note = inbox / "note.md"
    original = "# Frozen notes\n\nThese are local knowledge.\n"
    note.write_text(original)
    config = {
        "key": "funds",
        "sources": [{"key": "inbox", "type": "directory", "path": "./inbox"}],
    }
    result = cli(tmp_path, config, "sync", "--stop-after", "plan")
    assert result.returncode == 0, result.stderr
    run_path = Path(json.loads(result.stdout)["run"])
    plan = json.loads((run_path.parent / "plan.json").read_text())
    assert plan["sources"] == ["inbox"]
    [row] = plan["documents"]
    assert row["action"] == "compile"
    assert not list((tmp_path / "corpus").rglob("*.md"))
    note.write_text("# Changed after planning\n")
    result = cli(
        tmp_path, config, "sync", "--resume", str(run_path), "--stop-after", "plan"
    )
    assert result.returncode == 0, result.stderr
    assert Path(row["raw_path"]).read_text() == original
    Path(row["raw_path"]).write_text("# Modified artifact\n")
    result = cli(
        tmp_path, config, "sync", "--resume", str(run_path), "--stop-after", "plan"
    )
    assert result.returncode != 0
    assert "plan input missing or changed" in result.stderr


def test_mixed_subscription_publishes_once_and_directory_refresh_stays_offline(
    tmp_path,
):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    note = inbox / "note.md"
    original = "# Manual instructions\n\nApprove the request before settlement.\n"
    note.write_text(original)
    config = {
        "key": "funds",
        "sources": [
            {
                "key": "team",
                "type": "yuque",
                "url": "https://example.invalid/team/book",
            },
            {"key": "inbox", "type": "directory", "path": "./inbox"},
        ],
    }
    paused = cli(tmp_path, config, "sync", "--stop-after", "plan", remote=True)
    assert paused.returncode == 0, paused.stderr
    run_path = Path(json.loads(paused.stdout)["run"])
    plan = json.loads((run_path.parent / "plan.json").read_text())
    assert plan["sources"] == ["team", "inbox"]
    assert {
        (row["source_key"], row["source_document_id"]) for row in plan["documents"]
    } == {
        ("team", "1"),
        ("team", "2"),
        ("inbox", "note.md"),
    }
    note.write_text("# Current instructions\n\nReject cancelled requests.\n")
    completed = cli(tmp_path, config, "sync", "--resume", str(run_path), remote=True)
    assert completed.returncode == 0, completed.stderr
    corpus = tmp_path / "corpus" / "funds"
    assert len(list(corpus.glob("*.md"))) == 3
    assert any(path.read_text() == original for path in corpus.glob("*.md"))
    assert (corpus / "graphify-out" / "graph.json").is_file()
    log = tmp_path / "commands.jsonl"
    before = len(log.read_text().splitlines())
    changed = cli(tmp_path, config, "sync", "--source", "inbox", remote=True)
    assert changed.returncode == 0, changed.stderr
    assert any(path.read_text() == note.read_text() for path in corpus.glob("*.md"))
    calls = [json.loads(line)[:2] for line in log.read_text().splitlines()[before:]]
    assert ["graphify", "extract"] in calls
    assert all(tool != "skylark" for tool, _ in calls)
    before = log.read_bytes()
    unchanged = cli(tmp_path, config, "sync", "--source", "inbox", remote=True)
    assert unchanged.returncode == 0, unchanged.stderr
    added = log.read_bytes()[len(before) :].splitlines()
    assert [json.loads(line)[:2] for line in added] == [["mdcompare", "catalog"]]
    before = log.read_bytes()
    note.unlink()
    absent = cli(tmp_path, config, "sync", "--source", "inbox", remote=True)
    assert absent.returncode == 0, absent.stderr
    assert len(list(corpus.glob("*.md"))) == 3
    assert log.read_bytes() == before


def test_failed_selected_source_does_not_commit_observations_or_make_plan(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "ready.md").write_text("# Ready to publish\n")
    config = {
        "key": "funds",
        "sources": [
            {"key": "ready", "type": "directory", "path": "./inbox"},
            {"key": "missing", "type": "directory", "path": "./missing"},
        ],
    }
    failed = cli(tmp_path, config, "sync", "--stop-after", "plan")
    assert failed.returncode == 1
    assert "source missing:" in failed.stderr
    assert not (tmp_path / "state/funds/state.json").exists()
    assert not list((tmp_path / "runs").rglob("plan.json"))
    healthy = cli(tmp_path, config, "sync", "--source", "ready", "--stop-after", "plan")
    assert healthy.returncode == 0, healthy.stderr


def test_yuque_ids_are_scoped_even_when_different_sources_return_the_same_id(tmp_path):
    url = "https://example.invalid/team/book"
    config = {
        "key": "funds",
        "sources": [
            {"key": "first", "type": "yuque", "url": url, "document_ids": ["1"]},
            {"key": "second", "type": "yuque", "url": url, "document_ids": ["1"]},
        ],
    }
    result = cli(tmp_path, config, "discover", remote=True)
    assert result.returncode == 0, result.stderr
    artifact = Path(json.loads(result.stdout)["differences"])
    rows = json.loads(artifact.read_text())["documents"]
    assert {(row["source_key"], row["source_document_id"]) for row in rows} == {
        ("first", "1"),
        ("second", "1"),
    }
    assert len({row["document_id"] for row in rows}) == 2


def test_directory_destination_conflict_is_visible_in_plan_and_does_not_overwrite(
    tmp_path,
):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "note.md").write_text("# Incoming\n")
    config = {
        "key": "funds",
        "sources": [{"key": "inbox", "type": "directory", "path": "./inbox"}],
    }
    discovery = cli(tmp_path, config, "discover")
    assert discovery.returncode == 0, discovery.stderr
    artifact = Path(json.loads(discovery.stdout)["differences"])
    [document] = json.loads(artifact.read_text())["documents"]
    destination = (
        tmp_path / "corpus" / "funds" / f"source-[{document['document_id']}].md"
    )
    destination.write_text("# Existing unregistered corpus file\n")
    result = cli(tmp_path, config, "sync", "--stop-after", "plan")
    assert result.returncode == 0, result.stderr
    run_path = Path(json.loads(result.stdout)["run"])
    plan = json.loads((run_path.parent / "plan.json").read_text())
    assert not plan["documents"]
    [conflict] = plan["conflicts"]
    assert conflict["reason"] == "CORPUS_DESTINATION_CONFLICT"
    assert Path(conflict["path"]) == destination
    assert destination.read_text() == "# Existing unregistered corpus file\n"


def test_paused_apply_owns_snapshot_after_purging_discovery(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "images").mkdir(parents=True)
    from PIL import Image

    original = inbox / "images" / "chart.png"
    Image.new("RGB", (2, 2), "red").save(original)
    original_bytes = original.read_bytes()
    note = inbox / "notes.md"
    note.write_text('# Chart\n\n<img src="images/chart.png" alt="Chart">\n')
    config = {
        "key": "funds",
        "sources": [{"key": "inbox", "type": "directory", "path": "./inbox"}],
    }
    discovery = cli(tmp_path, config, "discover", remote=True)
    assert discovery.returncode == 0, discovery.stderr
    observed = json.loads(discovery.stdout)
    paused = cli(
        tmp_path,
        config,
        "apply",
        "--input",
        observed["differences"],
        "--stop-after",
        "plan",
        remote=True,
    )
    assert paused.returncode == 0, paused.stderr
    run_path = Path(json.loads(paused.stdout)["run"])
    plan = json.loads((run_path.parent / "plan.json").read_text())
    [document] = plan["documents"]
    assert Path(document["raw_path"]).is_relative_to(run_path.parent)
    [image] = document["resources"]
    assert Path(image["path"]).read_bytes() == original_bytes

    purged = cli(tmp_path, config, "purge", "--run", observed["run"], remote=True)
    assert purged.returncode == 0, purged.stderr
    assert not Path(observed["run"]).exists()
    frozen_image = Path(image["path"])
    frozen_image.write_bytes(b"modified frozen image")
    invalid = cli(tmp_path, config, "apply", "--resume", str(run_path), remote=True)
    assert invalid.returncode == 1
    assert "plan input missing or changed" in invalid.stderr
    frozen_image.write_bytes(original_bytes)
    Image.new("RGB", (2, 2), "blue").save(original)
    completed = cli(tmp_path, config, "apply", "--resume", str(run_path), remote=True)
    assert completed.returncode == 0, completed.stderr
    [published] = list((tmp_path / "corpus/funds").glob("*.md"))
    rendered = published.read_text()
    retained = Path(unquote(rendered.split('src="', 1)[1].split('"', 1)[0]))
    assert retained.read_bytes() == original_bytes
    assert original.read_bytes() != original_bytes

    new_scan = cli(tmp_path, config, "discover", remote=True)
    assert new_scan.returncode == 0, new_scan.stderr
    source = json.loads(new_scan.stdout)
    [incoming] = json.loads(Path(source["differences"]).read_text())["documents"]
    assert incoming["version"] != document["version"]
