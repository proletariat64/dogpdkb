"""Existing knowledge-base initialization through the installed CLI seam."""

import json
import os
import subprocess
import sys

import pytest


def run_init(tmp_path, *extra):
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(("DOGPDKB_", "SKYLARK_", "UTOO_", "GRAPHIFY_")):
            del env[name]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "init-state",
            "--config",
            str(tmp_path / "dogpdkb.toml"),
            "--book",
            "fund",
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def corpus_config(tmp_path):
    (tmp_path / "dogpdkb.toml").write_text(
        '[source]\nbooks_file = "books.json"\n'
        '[paths]\ncorpus = "corpus"\n'
        '[skylark]\nmcp_url = "https://example.invalid/mcp"\n'
    )
    (tmp_path / "books.json").write_text(
        json.dumps([{"key": "fund", "url": "https://example.invalid/funds/book"}])
    )
    corpus = tmp_path / "corpus/fund"
    graph = corpus / "graphify-out"
    graph.mkdir(parents=True)
    return corpus, graph


def test_initializes_existing_cleaned_corpus_without_reextracting(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    name = "Payments-2026-10-27-[42].md"
    (corpus / name).write_text("# Payments\nExisting cleaned knowledge.\n")
    (graph / "graph.json").write_text(
        json.dumps({"nodes": [{"id": "payment", "source_file": name}], "links": []})
    )
    (graph / "Report-2026-10-27-[99].md").write_text("not a corpus document")
    before = (graph / "graph.json").read_bytes()
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["counts"]["documents"] == 1
    state = json.loads((corpus / ".sync-state.json").read_text())
    record = state["books"]["fund"]["documents"]["42"]
    assert record["baseline"]["content_kind"] == "historical_enriched"
    assert record["baseline"]["content_updated_at"] is None
    assert record["baseline"]["time_precision"] == "day"
    assert record["baseline"]["graph_coverage"] == "existing_source"
    assert record["observed"] is None
    assert (graph / "graph.json").read_bytes() == before


def test_duplicate_ids_are_visible_without_losing_unique_sources(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    for name in [
        "Old-2026-10-27-[42].md",
        "Other-2026-10-28-[42].md",
        "Known-2026-10-27-[43].md",
    ]:
        (corpus / name).write_text("# Kept source\n")
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["counts"]["duplicate_ids"] == 1
    records = json.loads((corpus / ".sync-state.json").read_text())["books"]["fund"][
        "documents"
    ]
    assert records["42"]["baseline"] is None
    assert records["42"]["pending"] == ["DUPLICATE_DOCUMENT_ID"]
    assert records["43"]["baseline"]["graph_coverage"] == "unknown"


def test_migrates_only_matching_legacy_precise_times_in_utc8(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    names = [
        "One-2026-11-10-[42].md",
        "Two-2026-11-10-[43].md",
        "Three-2026-11-10-[44].md",
    ]
    for name in names:
        (corpus / name).write_text("# Existing\n")
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    (corpus / ".sync-state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "documents": {
                    "42": {
                        "file": names[0],
                        "content_updated_at": "2026-11-09T18:30:00Z",
                    },
                    "43": {
                        "file": names[1],
                        "content_updated_at": "2026-11-10T02:30:00",
                    },
                    "44": {
                        "file": "unrelated.md",
                        "content_updated_at": "2026-11-10T02:30:00+08:00",
                    },
                },
            }
        )
    )
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    records = json.loads((corpus / ".sync-state.json").read_text())["books"]["fund"][
        "documents"
    ]
    assert (
        records["42"]["baseline"]["content_updated_at"] == "2026-11-10T02:30:00+08:00"
    )
    assert records["42"]["baseline"]["version"]["value"] == "2026-11-09T18:30:00Z"
    assert records["43"]["baseline"]["version"]["default_timezone"] is True
    assert records["44"]["baseline"]["content_updated_at"] is None


def test_cannot_initialize_over_an_unfinished_plan(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    path = corpus / ".sync-state.json"
    path.write_text(
        json.dumps({"version": 2, "active_run": {"plan": "unfinished"}, "books": {}})
    )
    before = path.read_bytes()
    result = run_init(tmp_path)
    assert result.returncode == 1
    assert "resume" in result.stderr
    assert path.read_bytes() == before


def test_migration_rejects_malformed_records_without_overwriting(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    (corpus / "One-2026-11-10-[42].md").write_text("existing")
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    path = corpus / ".sync-state.json"
    path.write_text(json.dumps({"version": 1, "documents": {"42": "invalid"}}))
    before = path.read_bytes()
    result = run_init(tmp_path)
    assert result.returncode == 1
    assert "invalid legacy document" in result.stderr
    assert "Traceback" not in result.stderr
    assert path.read_bytes() == before


def test_reinitialization_clears_a_resolved_duplicate_only(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    first = corpus / "One-2026-11-10-[42].md"
    second = corpus / "Two-2026-11-10-[42].md"
    first.write_text("existing")
    second.write_text("duplicate")
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    assert run_init(tmp_path).returncode == 0
    second.unlink()
    assert run_init(tmp_path).returncode == 0
    record = json.loads((corpus / ".sync-state.json").read_text())["books"]["fund"][
        "documents"
    ]["42"]
    assert record["pending"] == []
    assert "conflicting_files" not in record


def test_initialization_reports_unmatched_root_markdown_without_fake_ids(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    (corpus / "Known-2026-11-10-[42].md").write_text("# Known\n")
    unmatched = corpus / "Operator-Notes-2026-11-10.md"
    unmatched.write_text("# Local operational notes\n")
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    before = unmatched.read_bytes()
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["counts"]["files"] == 2
    assert report["counts"]["documents"] == 1
    assert report["counts"]["unmatched_files"] == 1
    book = json.loads((corpus / ".sync-state.json").read_text())["books"]["fund"]
    assert book["unmatched_files"] == [unmatched.name]
    assert list(book["documents"]) == ["42"]
    assert unmatched.read_bytes() == before


def test_reinitialization_accepts_url_slash_and_preserves_existing_name(tmp_path):
    corpus, graph = corpus_config(tmp_path)
    (graph / "graph.json").write_text('{"nodes": [], "links": []}')
    books = tmp_path / "books.json"
    rows = json.loads(books.read_text())
    rows[0]["name"] = "Funds"
    books.write_text(json.dumps(rows))
    assert run_init(tmp_path).returncode == 0
    rows[0]["url"] += "/"
    rows[0].pop("name")
    books.write_text(json.dumps(rows))
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    book = json.loads((corpus / ".sync-state.json").read_text())["books"]["fund"]
    assert book["name"] == "Funds"


@pytest.mark.parametrize("original_survives", [False, True])
def test_duplicate_resolution_preserves_registered_baseline_identity(
    tmp_path, original_survives
):
    corpus, graph = corpus_config(tmp_path)
    first = corpus / "One-2026-11-10-[42].md"
    second = corpus / "Two-2026-11-10-[42].md"
    first.write_text("# Original knowledge\n")
    (graph / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [{"id": "original", "source_file": first.name}],
                "links": [],
            }
        )
    )
    before = (graph / "graph.json").read_bytes()
    assert run_init(tmp_path).returncode == 0
    second.write_text("# Different knowledge\n")
    assert run_init(tmp_path).returncode == 0
    (second if original_survives else first).unlink()
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    record = json.loads((corpus / ".sync-state.json").read_text())["books"]["fund"][
        "documents"
    ]["42"]
    assert record["baseline"]["file"] == first.name
    assert record["baseline"]["source"]["path"] == first.name
    if original_survives:
        assert record["pending"] == []
        assert "conflicting_files" not in record
    else:
        assert "BASELINE_SOURCE_CONFLICT" in record["pending"]
        assert record["conflicting_files"] == [second.name]
        assert json.loads(result.stdout)["counts"]["baseline_source_conflicts"] == 1
    assert (graph / "graph.json").read_bytes() == before


@pytest.mark.parametrize("graph_current", [True, False])
def test_reinitialization_accepts_a_published_corpus_and_graph_version(
    tmp_path, graph_current
):
    corpus, graph = corpus_config(tmp_path)
    first = corpus / "One-2026-11-10-[42].md"
    first.write_text("# Original knowledge\n")
    (graph / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [{"id": "original", "source_file": first.name}],
                "links": [],
            }
        )
    )
    assert run_init(tmp_path).returncode == 0
    second = corpus / "Two-2026-11-10-[42].md"
    first.rename(second)
    second.write_text("# Published knowledge\n")
    state_path = corpus / ".sync-state.json"
    state = json.loads(state_path.read_text())
    record = state["books"]["fund"]["documents"]["42"]
    version = {"utc": "2026-11-10T12:00:00+08:00"}
    raw = {"stage": "raw_export", "path": "published-raw.md"}
    (corpus / "published-raw.md").write_text("# Published knowledge\n")
    record["corpus"] = {
        "status": "published",
        "file": second.name,
        "raw": raw,
        "version": version,
    }
    record["graph"] = {
        "status": "covered",
        "coverage": "approved_whole_document",
        "raw": raw,
        "version": version if graph_current else {"utc": "2026-11-10T10:00:00+08:00"},
    }
    record["steps"] = {
        "corpus": {"status": "completed", "file": second.name, "version": version},
        "graph_publish": {"status": "completed", "version": version},
    }
    state_path.write_text(json.dumps(state))
    (graph / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [{"id": "published", "source_file": second.name}],
                "links": [],
            }
        )
    )
    graph_before = (graph / "graph.json").read_bytes()
    result = run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    record = json.loads(state_path.read_text())["books"]["fund"]["documents"]["42"]
    if graph_current:
        assert record["pending"] == []
        assert "conflicting_files" not in record
    else:
        assert record["pending"] == ["BASELINE_SOURCE_CONFLICT"]
    assert record["baseline"]["file"] == first.name
    assert record["corpus"]["file"] == second.name
    assert (graph / "graph.json").read_bytes() == graph_before
