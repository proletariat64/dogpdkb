import pytest

from scripts import graph_sync


def old_graph():
    return {
        "directed": True,
        "graph": {},
        "nodes": [
            {
                "id": "outside",
                "label": "Outside",
                "source_file": "outside-[9].md",
                "community": 1,
            },
            {
                "id": "stable",
                "label": "Same concept",
                "source_file": "old-[1].md",
                "community": 2,
            },
            {
                "id": "deleted",
                "label": "Deleted",
                "source_file": "gone-[2].md",
                "community": 3,
            },
        ],
        "links": [
            {"source": "outside", "target": "stable", "source_file": "outside-[9].md"},
            {"source": "outside", "target": "deleted", "source_file": "outside-[9].md"},
        ],
        "hyperedges": [],
    }


def test_merge_preserves_identity_and_unrelated_records_but_removes_deleted():
    fresh = {
        "nodes": [
            {"id": "fresh", "label": "Same concept", "source_file": "new-[1].md"},
            {"id": "outside", "label": "Collision", "source_file": "new-[1].md"},
        ],
        "edges": [
            {"source": "fresh", "target": "outside", "source_file": "new-[1].md"}
        ],
        "hyperedges": [],
    }

    candidate, labels, audit = graph_sync.merge_graph_records(
        old_graph(),
        fresh,
        current_files=["new-[1].md"],
        old_files=["old-[1].md"],
        deleted_files=["gone-[2].md"],
        source_urls={"new-[1].md": "https://example.invalid/new"},
        captured_at="2026-11-02T00:00:00+00:00",
        labels={"1": "Outside", "2": "Old", "3": "Deleted"},
    )

    nodes = {node["id"]: node for node in candidate["nodes"]}
    assert nodes["outside"] == old_graph()["nodes"][0]
    assert "stable" in nodes
    assert nodes["stable"]["source_file"] == "new-[1].md"
    assert nodes["stable"]["community"] == 2
    assert "deleted" not in nodes
    assert not any(edge["target"] == "deleted" for edge in candidate["links"])
    assert any(
        edge["source"] == "outside" and edge["target"] == "stable"
        for edge in candidate["links"]
    )
    assert audit["id_collisions_namespaced"] == 1
    assert any(
        node["label"] == "Collision" and node["id"] != "outside"
        for node in nodes.values()
    )
    assert labels["2"] == "Old"


def test_merge_supports_deletion_only_without_extraction():
    candidate, _, audit = graph_sync.merge_graph_records(
        old_graph(),
        {"nodes": [], "edges": [], "hyperedges": []},
        current_files=[],
        old_files=[],
        deleted_files=["gone-[2].md"],
        source_urls={},
        captured_at="2026-11-02T00:00:00+00:00",
        labels={"1": "Outside", "2": "Old", "3": "Deleted"},
    )

    assert {node["id"] for node in candidate["nodes"]} == {"outside", "stable"}
    assert candidate["links"] == [
        {"source": "outside", "target": "stable", "source_file": "outside-[9].md"}
    ]
    assert audit["deleted_sources"] == 1


def test_merge_rejects_extraction_from_outside_sync_scope():
    with pytest.raises(ValueError, match="out-of-scope"):
        graph_sync.merge_graph_records(
            old_graph(),
            {"nodes": [{"id": "bad", "source_file": "other-[8].md"}], "edges": []},
            current_files=["new-[1].md"],
            old_files=["old-[1].md"],
            deleted_files=[],
            source_urls={},
            captured_at="2026-11-02T00:00:00+00:00",
            labels={},
        )


def test_cross_document_collision_reference_uses_referenced_node():
    old = {
        "directed": True,
        "graph": {},
        "nodes": [
            {"id": "shared", "label": "Protected", "source_file": "outside-[9].md"}
        ],
        "links": [],
        "hyperedges": [],
    }
    fresh = {
        "nodes": [
            {"id": "shared", "label": "A", "source_file": "a-[1].md"},
            {"id": "other", "label": "B", "source_file": "b-[2].md"},
        ],
        "edges": [{"source": "other", "target": "shared", "source_file": "b-[2].md"}],
        "hyperedges": [],
    }

    candidate, _, _ = graph_sync.merge_graph_records(
        old,
        fresh,
        current_files=["a-[1].md", "b-[2].md"],
        old_files=[],
        deleted_files=[],
        source_urls={},
        captured_at="2026-11-02T00:00:00+00:00",
        labels={},
    )

    edge = candidate["links"][0]
    assert edge["target"] != "shared"
    nodes = {node["id"]: node for node in candidate["nodes"]}
    assert nodes[edge["target"]]["source_file"] == "a-[1].md"


def test_retained_reference_uses_current_source_and_member_aliases():
    old = old_graph()
    old["hyperedges"] = [
        {
            "members": ["outside", "stable"],
            "source_file": "outside-[9].md",
        }
    ]
    fresh = {
        "nodes": [{"id": "fresh", "label": "Changed", "source_file": "new-[1].md"}],
        "edges": [],
        "hyperedges": [{"nodes": ["fresh", "missing"], "source_file": "new-[1].md"}],
    }

    candidate, _, _ = graph_sync.merge_graph_records(
        old,
        fresh,
        current_files=["new-[1].md"],
        old_files=["old-[1].md"],
        deleted_files=["gone-[2].md"],
        source_urls={},
        captured_at="2026-11-02T00:00:00+00:00",
        labels={"1": "Outside", "2": "Old", "3": "Deleted"},
    )

    nodes = {node["id"]: node for node in candidate["nodes"]}
    assert nodes["stable"]["source_file"] == "new-[1].md"
    assert nodes["stable"]["retained_reference"] is True
    assert candidate["hyperedges"] == [
        {"members": ["outside", "stable"], "source_file": "outside-[9].md"}
    ]
