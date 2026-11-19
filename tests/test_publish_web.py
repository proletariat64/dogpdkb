import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import publish_web


def test_explicit_web_sources_do_not_require_enabled_sync_book(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Title-2026-11-12-[42].md").write_text("Current body")
    graph = tmp_path / "graph.json"
    graph.write_text('{"nodes":[],"links":[]}')
    (tmp_path / "books.json").write_text(
        '[{"key":"disabled","enabled":false,"sources":[{"type":"yuque","key":"team","url":"https://example.invalid/disabled"}],"corpus":"missing"}]'
    )
    settings = tmp_path / "instance.toml"
    settings.write_text('[source]\nbooks_file="books.json"\n')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "publish-web",
            "--config",
            str(settings),
            "--input",
            str(corpus),
            "--graph",
            str(graph),
            "--output",
            str(tmp_path / "web"),
            "--book-url",
            "https://example.invalid/explicit",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "web/catalog.json").is_file()


def test_web_snapshots_follow_current_corpus_and_graph(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    name = "Whale__Current-document-2026-11-01-[1].md"
    (corpus / name).write_text("current")
    graph = {
        "nodes": [
            {
                "id": "node-1",
                "label": "Current concept",
                "source_file": name,
                "community": 1397,
                "community_name": "Whale",
            },
            {
                "id": "orphan",
                "label": "Old deleted document",
                "source_file": "old-[2].md",
                "community": 2,
            },
        ],
        "links": [],
        "hyperedges": [],
    }

    catalog = publish_web.build_catalog(
        corpus, graph, {"name": "Book", "url": "https://example.invalid/book"}
    )
    layered = publish_web.build_map(graph, catalog)
    publish_web.write_snapshot(tmp_path / "web" / "catalog.json", catalog)
    publish_web.write_snapshot(tmp_path / "web" / "map" / "map-data.json", layered)

    assert catalog["stats"]["documents"] == 1
    assert [doc["file"] for doc in catalog["documents"]] == [name]
    assert catalog["documents"][0]["concepts"] == ["Current concept"]
    assert catalog["topics"][0]["count"] == 1
    assert layered["stats"]["rawNodes"] == 2
    assert layered["details"]["1397"]["documents"][0]["file"] == name
    saved = json.loads((tmp_path / "web" / "catalog.json").read_text())
    assert saved["stats"] == catalog["stats"]
    assert (tmp_path / "web" / "catalog.json").stat().st_mode & 0o777 == 0o644


def test_missing_corpus_preserves_existing_web_snapshots(tmp_path, monkeypatch):
    import sys

    import pytest

    graph = tmp_path / "graph.json"
    graph.write_text('{"nodes": [], "links": []}')
    output = tmp_path / "web"
    (output / "map").mkdir(parents=True)
    catalog = output / "catalog.json"
    layered = output / "map/map-data.json"
    catalog.write_text('{"documents": ["keep"]}')
    layered.write_text('{"nodes": ["keep"]}')
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_web",
            "--input",
            str(tmp_path / "missing"),
            "--graph",
            str(graph),
            "--output",
            str(output),
            "--book-url",
            "https://example.invalid/book",
        ],
    )
    with pytest.raises(SystemExit) as error:
        publish_web.main()
    assert error.value.code == 2
    assert json.loads(catalog.read_text()) == {"documents": ["keep"]}
    assert json.loads(layered.read_text()) == {"nodes": ["keep"]}


@pytest.mark.parametrize("failed_name", ["catalog.json", "map-data.json"])
def test_pair_generation_failure_preserves_both_snapshots(
    tmp_path, monkeypatch, failed_name
):
    output = tmp_path / "web"
    (output / "map").mkdir(parents=True)
    old_catalog = output / "catalog.json"
    old_map = output / "map/map-data.json"
    old_catalog.write_text('{"generation":"old"}')
    old_map.write_text('{"generation":"old"}')
    write = publish_web.write_snapshot

    def fail(path, payload):
        if path.name == failed_name:
            raise OSError("injected snapshot write failure")
        write(path, payload)

    monkeypatch.setattr(publish_web, "write_snapshot", fail)
    with pytest.raises(OSError, match="snapshot write failure"):
        publish_web.publish_snapshots(
            output, {"generation": "new"}, {"generation": "new"}
        )
    assert json.loads(old_catalog.read_text()) == {"generation": "old"}
    assert json.loads(old_map.read_text()) == {"generation": "old"}


def test_pair_switch_is_atomic_and_recovers_after_interruption(tmp_path, monkeypatch):
    output = tmp_path / "web"
    (output / "map").mkdir(parents=True)
    catalog = output / "catalog.json"
    layered = output / "map/map-data.json"
    catalog.write_text('{"generation":"old"}')
    layered.write_text('{"generation":"old"}')
    replace = Path.replace

    def interrupt_switch(path, target):
        if Path(target).name == "current":
            raise KeyboardInterrupt("injected before pointer switch")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupt_switch)
        with pytest.raises(KeyboardInterrupt):
            publish_web.publish_snapshots(
                output, {"generation": "new"}, {"generation": "new"}
            )
    assert json.loads(catalog.read_text())["generation"] == "old"
    assert json.loads(layered.read_text())["generation"] == "old"
    publish_web.publish_snapshots(output, {"generation": "new"}, {"generation": "new"})
    assert catalog.is_symlink() and layered.is_symlink()
    assert catalog.resolve().parent == layered.resolve().parent
    assert json.loads(catalog.read_text())["generation"] == "new"
    assert json.loads(layered.read_text())["generation"] == "new"
    assert catalog.stat().st_mode & 0o777 == 0o644
