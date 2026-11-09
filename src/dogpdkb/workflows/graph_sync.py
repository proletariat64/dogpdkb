"""Merge a bounded semantic extraction into an existing graphify graph."""

import copy
import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DOC_ID_RE = re.compile(r"\[(\d+)\]\.md$")


def doc_id(source: str | Path | None) -> str | None:
    match = DOC_ID_RE.search(str(source or ""))
    return match.group(1) if match else None


def source_name(item: dict[str, Any]) -> str:
    return Path(str(item.get("source_file") or "")).name


def validate_extracted_records(fresh: dict[str, Any], old: dict[str, Any]) -> None:
    """Reject invalid adapter/cache records before merge can discard them."""
    if any(
        not isinstance(fresh.get(kind), list)
        for kind in ("nodes", "edges", "hyperedges")
    ):
        raise ValueError("semantic extraction has invalid record collections")
    identities = {
        node.get("id") for node in old.get("nodes", []) if isinstance(node, dict)
    }
    owned_ids = set()
    for node in fresh["nodes"]:
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("id"), str)
            or not node["id"]
        ):
            raise ValueError("semantic extraction contains an invalid node identity")
        owned_key = (source_name(node), node["id"])
        if owned_key in owned_ids:
            raise ValueError(f"duplicate extracted node identity: {owned_key}")
        owned_ids.add(owned_key)
        identities.add(node["id"])
    for edge in fresh["edges"]:
        if (
            not isinstance(edge, dict)
            or not isinstance(edge.get("source"), str)
            or not isinstance(edge.get("target"), str)
            or edge["source"] not in identities
            or edge["target"] not in identities
        ):
            raise ValueError("semantic extraction contains a dangling edge")
    for hyperedge in fresh["hyperedges"]:
        if not isinstance(hyperedge, dict):
            raise TypeError("semantic extraction contains an invalid hyperedge")
        members = next(
            (
                hyperedge[key]
                for key in ("nodes", "members", "node_ids")
                if key in hyperedge
            ),
            [],
        )
        if (
            not isinstance(members, list)
            or any(not isinstance(member, str) for member in members)
            or not set(members) <= identities
        ):
            raise ValueError("semantic extraction contains a dangling hyperedge")


def _namespace_id(source: str, node_id: str) -> str:
    digest = hashlib.sha256(f"{source}\0{node_id}".encode()).hexdigest()[:18]
    return f"sync_{digest}"


def _normalize_fresh(
    fresh: dict[str, Any],
    current_files: list[str],
    source_urls: dict[str, str],
    captured_at: str,
) -> dict[str, Any]:
    current_by_id = {doc_id(name): name for name in current_files}
    if None in current_by_id or len(current_by_id) != len(current_files):
        raise ValueError("current sync files must have unique Yuque document IDs")
    current_names = set(current_files)
    normalized = {"nodes": [], "edges": [], "hyperedges": []}
    for kind in ("nodes", "edges", "hyperedges"):
        for original in fresh.get(kind, []):
            item = copy.deepcopy(original)
            source = source_name(item)
            identity = doc_id(source)
            if identity in current_by_id:
                source = current_by_id[identity]
            if source not in current_names:
                raise ValueError(f"out-of-scope {kind} source: {source}")
            item["source_file"] = source
            item["source_url"] = source_urls.get(source)
            item["captured_at"] = captured_at
            normalized[kind].append(item)
    covered = {
        item["source_file"]
        for kind in ("nodes", "hyperedges")
        for item in normalized[kind]
    }
    missing = current_names - covered
    if missing:
        raise ValueError(f"semantic extraction did not cover: {sorted(missing)}")
    return normalized


def merge_graph_records(
    old: dict[str, Any],
    fresh: dict[str, Any],
    *,
    current_files: list[str],
    old_files: list[str],
    deleted_files: list[str],
    source_urls: dict[str, str],
    captured_at: str,
    labels: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    current_names = set(current_files)
    old_names = set(old_files)
    deleted_names = set(deleted_files)
    affected = current_names | old_names | deleted_names
    deleted_ids = {doc_id(name) for name in deleted_names}
    current_by_doc = {doc_id(name): name for name in current_names}
    new = _normalize_fresh(fresh, current_files, source_urls, captured_at)

    old_nodes = {node["id"]: node for node in old.get("nodes", [])}
    protected = {
        node["id"] for node in old.get("nodes", []) if source_name(node) not in affected
    }
    # Historical references must retain their owner even when that owner's
    # document is in this delta. Stable identities reclaim these IDs below.
    claimed = set(old_nodes)
    collision_remap: dict[tuple[str, str], str] = {}
    collision_nodes = []
    for node in new["nodes"]:
        source = node["source_file"]
        original_id = str(node["id"])
        key = (source, original_id)
        if key in collision_remap:
            continue
        if original_id in claimed:
            collision_remap[key] = _namespace_id(source, original_id)
            claimed.add(collision_remap[key])
        else:
            claimed.add(original_id)
        collision_nodes.append(node)

    fresh_nodes_by_id = {}
    for node in collision_nodes:
        original_id = str(node["id"])
        node["id"] = collision_remap.get(
            (node["source_file"], original_id), original_id
        )
        fresh_nodes_by_id[node["id"]] = node

    old_identity = defaultdict(set)
    new_identity = defaultdict(set)
    for node in old.get("nodes", []):
        identity = doc_id(source_name(node))
        if source_name(node) in affected and identity not in deleted_ids:
            old_identity[(identity, str(node.get("label") or "").strip())].add(
                node["id"]
            )
    for node in fresh_nodes_by_id.values():
        new_identity[
            (doc_id(node["source_file"]), str(node.get("label") or "").strip())
        ].add(node["id"])
    identity_remap = {}
    for key, previous_ids in old_identity.items():
        fresh_ids = new_identity.get(key, set())
        if key[1] and len(previous_ids) == len(fresh_ids) == 1:
            previous_id = next(iter(previous_ids))
            fresh_id = next(iter(fresh_ids))
            if previous_id != fresh_id and previous_id not in fresh_nodes_by_id:
                identity_remap[fresh_id] = previous_id

    collision_targets = defaultdict(set)
    for (_source, original_id), target in collision_remap.items():
        collision_targets[original_id].add(target)

    def remap_fresh_reference(item: dict[str, Any], node_id: str) -> str:
        original_id = str(node_id)
        own_key = (item["source_file"], original_id)
        if own_key in collision_remap:
            namespaced = collision_remap[own_key]
        elif (
            original_id in fresh_nodes_by_id
            and fresh_nodes_by_id[original_id]["source_file"] == item["source_file"]
        ):
            namespaced = original_id
        else:
            targets = set(collision_targets.get(original_id, set()))
            if original_id in fresh_nodes_by_id:
                targets.add(original_id)
            if len(targets) > 1:
                raise ValueError(f"ambiguous cross-document reference: {original_id}")
            namespaced = next(iter(targets)) if targets else original_id
        return identity_remap.get(namespaced, namespaced)

    def members(hyperedge: dict[str, Any]) -> list[str]:
        for key in ("nodes", "members", "node_ids"):
            if key in hyperedge:
                return hyperedge[key]
        return []

    remapped_nodes = {}
    for node in fresh_nodes_by_id.values():
        node["id"] = identity_remap.get(node["id"], node["id"])
        remapped_nodes[node["id"]] = node
    new["nodes"] = list(remapped_nodes.values())
    for edge in new["edges"]:
        edge["source"] = remap_fresh_reference(edge, edge["source"])
        edge["target"] = remap_fresh_reference(edge, edge["target"])
    for hyperedge in new["hyperedges"]:
        for key in ("nodes", "members", "node_ids"):
            if key in hyperedge:
                hyperedge[key] = [
                    remap_fresh_reference(hyperedge, node_id)
                    for node_id in hyperedge[key]
                ]

    nodes = {
        node["id"]: copy.deepcopy(node)
        for node in old.get("nodes", [])
        if node["id"] in protected
    }
    nodes.update({node["id"]: node for node in new["nodes"]})

    kept_old_edges = [
        copy.deepcopy(edge)
        for edge in old.get("links", [])
        if source_name(edge) not in affected
    ]
    kept_old_hyperedges = [
        copy.deepcopy(hyperedge)
        for hyperedge in old.get("hyperedges", [])
        if source_name(hyperedge) not in affected
    ]
    needed_references = {
        endpoint
        for edge in kept_old_edges
        for endpoint in (edge["source"], edge["target"])
    }
    for hyperedge in kept_old_hyperedges:
        needed_references.update(members(hyperedge))
    retained = 0
    for node_id in sorted(needed_references - set(nodes)):
        previous = old_nodes.get(node_id)
        if previous is None:
            raise ValueError(f"unknown historical reference: {node_id}")
        previous_source = source_name(previous)
        identity = doc_id(previous_source)
        if identity in deleted_ids:
            continue
        if previous_source not in affected:
            raise ValueError(f"reference would retain unrelated node: {node_id}")
        if identity not in current_by_doc:
            raise ValueError(f"reference has no current source document: {node_id}")
        restored = copy.deepcopy(previous)
        restored.update(
            source_file=current_by_doc[identity],
            retained_reference=True,
            current_source_file=current_by_doc[identity],
            verification="unverified",
        )
        nodes[node_id] = restored
        retained += 1

    if any(
        edge["source"] not in nodes or edge["target"] not in nodes
        for edge in new["edges"]
    ):
        raise ValueError("fresh edge is dangling after source replacement")
    if any(set(members(hyperedge)) > set(nodes) for hyperedge in new["hyperedges"]):
        raise ValueError("fresh hyperedge is dangling after source replacement")

    def endpoint_key(edge: dict[str, Any]) -> tuple[str, str]:
        return (
            (edge["source"], edge["target"])
            if old.get("directed")
            else tuple(sorted((edge["source"], edge["target"])))
        )

    unrelated_endpoints = {endpoint_key(edge) for edge in kept_old_edges}
    if any(endpoint_key(edge) in unrelated_endpoints for edge in new["edges"]):
        raise ValueError("fresh edge would overwrite an unrelated source record")
    edge_map = {}
    for edge in kept_old_edges + new["edges"]:
        if edge["source"] not in nodes or edge["target"] not in nodes:
            continue
        key = (
            (edge["source"], edge["target"])
            if old.get("directed")
            else tuple(sorted((edge["source"], edge["target"])))
        )
        edge_map[key] = edge
    ids = set(nodes)
    hyperedges = [
        hyperedge
        for hyperedge in kept_old_hyperedges + new["hyperedges"]
        if set(members(hyperedge)) <= ids
    ]

    normalized_labels = {str(key): value for key, value in labels.items()}
    prior_by_doc = defaultdict(Counter)
    for node in old.get("nodes", []):
        if node.get("community") is not None and source_name(node) in affected:
            prior_by_doc[doc_id(source_name(node))][int(node["community"])] += 1
    next_community = max((int(key) for key in normalized_labels), default=-1) + 1
    file_community = {}
    for name in sorted(current_names):
        prior = prior_by_doc.get(doc_id(name))
        fallback_label = re.sub(
            r"-\d{4}-\d{2}-\d{2}-\[\d+\]\.md$", "", name.split("__")[-1]
        )
        if prior:
            community = prior.most_common(1)[0][0]
            normalized_labels.setdefault(str(community), fallback_label)
        else:
            community = next_community
            next_community += 1
            normalized_labels[str(community)] = fallback_label
        file_community[name] = community
    for node in new["nodes"]:
        community = file_community[node["source_file"]]
        node["community"] = community
        node["community_name"] = normalized_labels[str(community)]
        node["norm_label"] = unicodedata.normalize(
            "NFKD", str(node.get("label") or "")
        ).lower()

    candidate = copy.deepcopy(old)
    candidate["nodes"] = list(nodes.values())
    candidate["links"] = list(edge_map.values())
    candidate["hyperedges"] = hyperedges
    candidate.setdefault("graph", {})["hyperedges"] = copy.deepcopy(hyperedges)
    candidate_ids = [node["id"] for node in candidate["nodes"]]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate graph contains duplicate node IDs")
    ids = set(candidate_ids)
    if any(
        edge["source"] not in ids or edge["target"] not in ids
        for edge in candidate["links"]
    ):
        raise ValueError("candidate graph contains dangling edges")
    if any(set(members(hyperedge)) > ids for hyperedge in hyperedges):
        raise ValueError("candidate graph contains dangling hyperedges")
    for node in old.get("nodes", []):
        if node["id"] in protected and nodes[node["id"]] != node:
            raise ValueError(f"unrelated node was modified: {node['id']}")
    audit = {
        "before": {
            "nodes": len(old.get("nodes", [])),
            "edges": len(old.get("links", [])),
            "hyperedges": len(old.get("hyperedges", [])),
        },
        "after": {
            "nodes": len(candidate["nodes"]),
            "edges": len(candidate["links"]),
            "hyperedges": len(hyperedges),
        },
        "changed_sources": len(current_names),
        "deleted_sources": len(deleted_names),
        "id_collisions_namespaced": len(collision_remap),
        "stable_identity_matches": len(identity_remap),
        "retained_historical_references": retained,
        "unrelated_nodes_preserved": len(protected),
    }
    return candidate, normalized_labels, audit
