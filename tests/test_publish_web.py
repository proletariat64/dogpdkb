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
