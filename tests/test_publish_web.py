import json
from pathlib import Path

from scripts import publish_web


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
