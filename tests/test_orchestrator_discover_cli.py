"""Discover contracts through real CLI processes and temporary Markdown."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.source_fixture import team_id

ROOT = Path(__file__).resolve().parents[1]


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *map(str, args)],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def setup(tmp_path):
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n'
    )
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "name": "Funds",
                    "sources": [
                        {
                            "key": "team",
                            "type": "yuque",
                            "url": "https://example.invalid/book",
                        }
                    ],
                    "corpus": "corpus",
                }
            ]
        )
    )
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Earlier-2026-11-01-[42].md").write_text("# Earlier knowledge\n")
    (corpus / "Newest-2026-11-10-[43].md").write_text("# Newest knowledge\n")
    result = cli("init-state", "--config", config)
    assert result.returncode == 0, result.stderr
    return config


def listing(tmp_path, documents, **extra):
    path = tmp_path / "listing.json"
    path.write_text(
        json.dumps(
            {
                "schema": "skylark-listing-1",
                "complete": True,
                "book": {
                    "url": "https://example.invalid/book",
                    "book_id": 8,
                    "items_count": len(documents),
                },
                "documents": documents,
                "toc": [],
                "captured_at": "2026-11-12T02:00:00Z",
                **extra,
            }
        )
    )
    return path


def difference(result):
    assert result.returncode == 0, result.stderr
    pointer = json.loads(result.stdout)
    return json.loads(Path(pointer["differences"]).read_text())


def test_first_listing_uses_cutoff_but_compares_existing_same_day(tmp_path):
    config = setup(tmp_path)
    path = listing(
        tmp_path,
        [
            {
                "document_id": "1",
                "title": "Historical missing",
                "version": "2026-11-09T10:00:00+08:00",
            },
            {
                "document_id": "2",
                "title": "Recent new",
                "version": "2026-11-10T00:00:00+08:00",
            },
            {
                "document_id": "42",
                "title": "Earlier",
                "version": "2026-11-01T12:00:00+08:00",
            },
            {
                "document_id": "43",
                "title": "Newest",
                "version": "2026-11-10T12:00:00+08:00",
            },
        ],
    )
    before = {p.name: p.read_bytes() for p in (tmp_path / "corpus").glob("*.md")}
    data = difference(cli("discover", "--config", config, "--listing", path))
    assert data["schema"] == "dogpdkb-differences-2"
    assert data["cutoffs"]["team"] == "2026-11-10"
    assert {row["source_document_id"] for row in data["documents"]} == {"2", "42", "43"}
    assert data["ignored"] == [team_id("1")]
    assert before == {
        p.name: p.read_bytes() for p in (tmp_path / "corpus").glob("*.md")
    }
    assert not list((tmp_path / "runs").rglob("*.md"))


def test_complete_snapshots_track_toc_move_and_absence_without_deletion(tmp_path):
    config = setup(tmp_path)
    rows = [
        {
            "document_id": "1",
            "title": "Historical",
            "version": "2026-11-01T12:00:00+08:00",
        }
    ]
    toc = [
        {"uuid": "section", "title": "Before", "type": "TITLE"},
        {
            "uuid": "source",
            "parent_uuid": "section",
            "doc_id": 1,
            "title": "Historical",
            "type": "DOC",
        },
    ]
    path = listing(tmp_path, rows, toc=toc)
    first = difference(cli("discover", "--config", config, "--listing", path))
    assert first["documents"] == []
    assert first["ignored"] == [team_id("1")]
    unchanged = difference(cli("discover", "--config", config, "--listing", path))
    assert unchanged["documents"] == []
    rows[0]["version"] = "2026-11-01T04:00:00Z"
    toc[0]["title"] = "After"
    path = listing(tmp_path, rows, toc=toc)
    moved = difference(cli("discover", "--config", config, "--listing", path))
    assert len(moved["documents"]) == 1
    selected = moved["documents"][0]
    assert selected["source_document_id"] == "1"
    assert selected["path"] == ["After", "Historical"]
    assert selected["reasons"] == ["path-changed"]
    assert moved["absent"] == sorted((team_id("42"), team_id("43")))
    assert (
        tmp_path / "corpus" / "Earlier-2026-11-01-[42].md"
    ).read_text() == "# Earlier knowledge\n"


def test_configured_skylark_only_lists_and_receives_gateway_configuration(tmp_path):
    config = setup(tmp_path)
    path = listing(
        tmp_path,
        [{"document_id": "2", "title": "New", "version": "2026-11-12T10:00:00+08:00"}],
    )
    calls = tmp_path / "calls.jsonl"
    tool = tmp_path / "skylark.py"
    tool.write_text("""import json, os, pathlib, shutil, sys
with pathlib.Path(sys.argv[2]).open("a") as stream:
    stream.write(json.dumps(sys.argv[3:]) + "\\n")
assert os.environ.get("SKYLARK_MCP_URL") == "https://example.invalid/gateway"
assert os.environ.get("UTOO_PROXY_BIN") == "configured-proxy"
assert sys.argv[3] == "listing", "body export is forbidden during discover"
shutil.copyfile(sys.argv[1], sys.argv[sys.argv.index("--output") + 1])
print("listing complete", flush=True)
""")
    with config.open("a") as stream:
        stream.write(
            '[skylark]\nmcp_url="https://example.invalid/gateway"\nproxy_bin="configured-proxy"\n'
        )
        stream.write(
            f"[tools]\nskylark={json.dumps([sys.executable, str(tool), str(path), str(calls)])}\n"
        )
    result = cli("discover", "--config", config, "--book", "funds")
    data = difference(result)
    assert [row["source_document_id"] for row in data["documents"]] == ["2"]
    assert "listing complete" in result.stderr
    assert [json.loads(line)[0] for line in calls.read_text().splitlines()] == [
        "listing"
    ]


def test_historical_filtered_source_stays_filtered_after_absence(tmp_path):
    config = setup(tmp_path)
    rows = [
        {
            "document_id": "1",
            "title": "Historical",
            "version": "2026-11-01T12:00:00+08:00",
        }
    ]
    path = listing(tmp_path, rows)
    assert (
        difference(cli("discover", "--config", config, "--listing", path))["documents"]
        == []
    )
    path = listing(tmp_path, [])
    difference(cli("discover", "--config", config, "--listing", path))
    path = listing(tmp_path, rows)
    returned = difference(cli("discover", "--config", config, "--listing", path))
    assert returned["documents"] == []


def test_unattempted_selection_survives_run_cleanup(tmp_path):
    config = setup(tmp_path)
    path = listing(
        tmp_path,
        [{"document_id": "2", "title": "New", "version": "2026-11-12T10:00:00+08:00"}],
    )
    first = difference(cli("discover", "--config", config, "--listing", path))
    assert [row["source_document_id"] for row in first["documents"]] == ["2"]
    shutil.rmtree(tmp_path / "runs")
    next_run = difference(cli("discover", "--config", config, "--listing", path))
    assert [row["source_document_id"] for row in next_run["documents"]] == ["2"]
    assert next_run["documents"][0]["reasons"] == ["new"]


@pytest.mark.parametrize("broken", ["incomplete", "count", "duplicate", "wrong-book"])
def test_invalid_full_listing_never_advances_observation(tmp_path, broken):
    config = setup(tmp_path)
    path = listing(
        tmp_path,
        [{"document_id": "2", "title": "New", "version": "2026-11-12T10:00:00+08:00"}],
    )
    difference(cli("discover", "--config", config, "--listing", path))
    state_path = tmp_path / "state" / "funds" / "state.json"
    before = state_path.read_bytes()
    data = json.loads(path.read_text())
    if broken == "incomplete":
        data["complete"] = False
    elif broken == "count":
        data["book"]["items_count"] = 2
    elif broken == "duplicate":
        data["documents"] *= 2
        data["book"]["items_count"] = 2
    else:
        data["book"]["url"] = "https://example.invalid/another-book"
    path.write_text(json.dumps(data))
    result = cli("discover", "--config", config, "--listing", path)
    assert result.returncode == 1
    assert state_path.read_bytes() == before
    status = cli("status", "--config", config, "--json")
    assert json.loads(status.stdout)["status"] == "failed"


def test_existing_corpus_requires_explicit_initial_registration(tmp_path):
    config = setup(tmp_path)
    (tmp_path / "state" / "funds" / "state.json").unlink()
    path = listing(
        tmp_path, [{"document_id": "1", "title": "Historical", "version": "2020-01-01"}]
    )
    result = cli("discover", "--config", config, "--listing", path)
    assert result.returncode == 1
    assert "init-state" in result.stderr
    assert not (tmp_path / "state" / "funds" / "state.json").exists()


def test_gateway_timeout_does_not_limit_complete_listing_duration(tmp_path):
    config = setup(tmp_path)
    path = listing(tmp_path, [])
    tool = tmp_path / "skylark.py"
    tool.write_text("""import os, shutil, sys, time
assert os.environ["SKYLARK_TIMEOUT_MS"] == "100"
time.sleep(0.2)
shutil.copyfile(sys.argv[1], sys.argv[sys.argv.index("--output") + 1])
""")
    with config.open("a") as stream:
        stream.write("[skylark]\ntimeout_seconds=0.1\n")
        stream.write(
            f"[tools]\nskylark={json.dumps([sys.executable, str(tool), str(path)])}\n"
        )
    result = difference(cli("discover", "--config", config))
    assert result["documents"] == []


def test_saved_tool_log_keeps_recoverable_command_with_credential_values_redacted(
    tmp_path,
):
    config = setup(tmp_path)
    path = listing(tmp_path, [])
    tool = tmp_path / "skylark.py"
    tool.write_text(
        'import shutil,sys\nshutil.copyfile(sys.argv[1],sys.argv[sys.argv.index("--output")+1])\n'
    )
    with config.open("a") as stream:
        stream.write(
            f"[tools]\nskylark={json.dumps([sys.executable, str(tool), str(path), '--token', 'fixture-private-token', '--password=fixture-private-password'])}\n"
        )
    result = cli("discover", "--config", config)
    data = difference(result)
    log = (Path(data["listings"]["team"]).with_suffix(".log")).read_text()
    assert "command:" in log and "listing --url" in log
    assert "--token" in log and "--password=" in log
    assert "[redacted]" in log
    assert "fixture-private-token" not in log
    assert "fixture-private-password" not in log


def test_legacy_whitelist_and_new_include_key_share_state(tmp_path):
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n'
    )
    books = tmp_path / "books.json"
    source = {
        "key": "funds",
        "name": "Small subscription",
        "sources": [
            {
                "key": "team",
                "type": "yuque",
                "url": "https://example.invalid/book",
                "document_ids": ["2"],
            }
        ],
        "corpus": "corpus",
    }
    books.write_text(json.dumps([source]))
    path = listing(
        tmp_path,
        [{"document_id": str(i), "title": str(i), "version": "v1"} for i in (1, 2, 3)],
    )
    result = cli("discover", "--config", config, "--listing", path)
    diff = difference(result)
    assert [r["source_document_id"] for r in diff["documents"]] == ["2"]
    # The full remote observation is not forged into a pretend one-document book.
    frozen = json.loads(Path(diff["listings"]["team"]).read_text())
    assert frozen["book"]["items_count"] == len(frozen["documents"]) == 3
    state_path = tmp_path / "state/funds/state.json"
    before = state_path.read_bytes()
    state = json.loads(before)
    assert set(state["documents"]) == {team_id("2")}
    assert state["book"]["sources"]["team"]["document_ids"] == ["2"]
    del source["sources"][0]["document_ids"]
    source["sources"][0]["include_document_ids"] = ["1", "2"]
    books.write_text(json.dumps([source]))
    changed = difference(cli("discover", "--config", config, "--listing", path))
    # ID 2 still has unconsumed pending work from the first discovery.
    assert {r["source_document_id"] for r in changed["documents"]} == {"1", "2"}
    assert not changed["absent"]
    assert json.loads(state_path.read_text())["book"]["sources"]["team"][
        "document_ids"
    ] == ["1", "2"]


@pytest.mark.parametrize(
    "field", ["include_document_ids", "exclude_document_ids", "document_ids"]
)
@pytest.mark.parametrize("scope", [None, [], ["2", "2"], [2], "2", [" "]])
def test_invalid_subscription_cannot_expand_to_entire_book(tmp_path, field, scope):
    config = tmp_path / "instance.toml"
    config.write_text('[source]\nbooks_file="books.json"\n')
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "sources": [
                        {
                            "key": "team",
                            "type": "yuque",
                            "url": "https://example.invalid/book",
                            field: scope,
                        }
                    ],
                }
            ]
        )
    )
    result = cli("discover", "--config", config)
    assert result.returncode == 1
    assert field in result.stderr


@pytest.mark.parametrize(
    ("kind", "scope", "message"),
    [
        (
            "yuque",
            {"include_document_ids": ["2"], "exclude_document_ids": ["3"]},
            "mutually exclusive",
        ),
        (
            "yuque",
            {"document_ids": ["2"], "exclude_document_ids": ["3"]},
            "mutually exclusive",
        ),
        (
            "yuque",
            {"document_ids": ["2"], "include_document_ids": ["2"]},
            "mutually exclusive",
        ),
        ("directory", {"exclude_document_ids": ["2"]}, "directory source"),
        ("directory", {"include_document_ids": ["2"]}, "directory source"),
    ],
)
def test_scope_is_unambiguous_and_only_for_yuque(tmp_path, kind, scope, message):
    cfg = tmp_path / "dogpdkb.toml"
    cfg.write_text("")
    endpoint = (
        {"url": "https://example.invalid/book"}
        if kind == "yuque"
        else {"path": "inbox"}
    )
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "sources": [{"key": "team", "type": kind, **endpoint, **scope}],
                }
            ]
        )
    )
    result = cli("discover", "--config", cfg)
    assert result.returncode == 1 and message in result.stderr
    assert not (tmp_path / "state").exists()


def test_subscription_init_state_does_not_mark_other_corpus_ids_absent(tmp_path):
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n'
    )
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "sources": [
                        {
                            "key": "team",
                            "type": "yuque",
                            "url": "https://example.invalid/book",
                            "document_ids": ["42"],
                        }
                    ],
                    "corpus": "corpus",
                }
            ]
        )
    )
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for ident in (42, 43):
        (corpus / f"Source-2026-11-12-[{ident}].md").write_text(
            "# Real corpus source\n"
        )
    assert cli("init-state", "--config", config).returncode == 0
    path = listing(
        tmp_path,
        [
            {"document_id": str(i), "title": str(i), "version": "2026-11-12T10:00:00Z"}
            for i in (42, 43)
        ],
    )
    diff = difference(cli("discover", "--config", config, "--listing", path))
    assert diff["absent"] == []
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert set(state["documents"]) == {team_id("42")}
    assert (
        corpus / "Source-2026-11-12-[43].md"
    ).read_text() == "# Real corpus source\n"
