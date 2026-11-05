"""Regenerate the knowledge-base catalog and layered map from local artifacts."""

import argparse
import collections
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})-\[(\d+)\]\.md$")
TOPIC_SPECS = [
    (
        1397,
        "Whale 专项",
        "项目规划",
        "全球资金网络升级的项目规划、战役拆解与实施脉络。",
    ),
    (0, "USD 美元", "跨境清算", "美元收单站、清算账户与跨境资金结算相关知识。"),
    (13, "One Portal", "平台产品", "平台户入驻、交易支持与汇率运营产品能力。"),
    (22, "内部户", "账户体系", "内部账户、账龄管理与账户监控产品体系。"),
    (3, "SWIFT (跨境)", "清算网络", "SWIFT 跨境清算网络、报文与银行账户规则。"),
    (19, "核算分录验证", "核算账务", "核算方案、分录验证与虚拟资金流。"),
    (20, "外汇兑换协议", "外汇产品", "收单站流入、清算与来账兑换协议。"),
    (1516, "流动性定价引擎", "流动性", "FTP 评价、监测、定价与流动性管理。"),
    (99, "外部清偿", "资金清算", "主体间协议、集中清算与外部清偿。"),
]


def group_for(category: str) -> str:
    if category == "收付款产品":
        return "收付款"
    if category == "Accounting":
        return "核算"
    if category == "资金方案中心管控":
        return "资金方案"
    if category.startswith("外汇"):
        return "外汇"
    if category == "Liquidity":
        return "流动性"
    if category == "Whale":
        return "Whale"
    if category == "Planning-and-progress-of-key-projects":
        return "项目规划"
    return "其他"


def build_catalog(
    corpus: Path, graph: dict[str, Any], book: dict[str, str]
) -> dict[str, Any]:
    concepts = collections.defaultdict(list)
    community_nodes = collections.defaultdict(list)
    for node in graph["nodes"]:
        source = node.get("source_file")
        label = node.get("label")
        if source and label:
            labels = concepts[Path(source).name]
            if label not in labels and len(labels) < 8:
                labels.append(label)
        if node.get("community") is not None:
            community_nodes[node["community"]].append(node)
    documents = []
    for path in corpus.glob("*.md"):
        match = NAME_RE.search(path.name)
        base = path.name[: match.start()] if match else path.stem
        parts = base.split("__")
        category = parts[0] if len(parts) > 1 else "未分类"
        documents.append(
            {
                "id": match.group(2) if match else "",
                "title": parts[-1],
                "file": path.name,
                "category": category,
                "group": group_for(category),
                "date": match.group(1) if match else "",
                "concepts": concepts.get(path.name, [])[:6],
            }
        )
    documents.sort(key=lambda doc: (doc["date"], doc["title"]), reverse=True)
    topics = []
    for community_id, name, domain, description in TOPIC_SPECS:
        nodes = community_nodes.get(community_id, [])
        labels = []
        for node in nodes:
            label = node.get("label")
            if label and label not in labels:
                labels.append(label)
            if len(labels) == 12:
                break
        topics.append(
            {
                "id": community_id,
                "name": name,
                "domain": domain,
                "description": description,
                "count": len(nodes),
                "labels": labels,
            }
        )
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "book": book,
        "stats": {
            "documents": len(documents),
            "nodes": len(graph["nodes"]),
            "edges": len(graph.get("links", [])),
            "hyperedges": len(graph.get("hyperedges", [])),
            "communities": len(community_nodes),
            "graph_commit": graph.get("built_at_commit", ""),
        },
        "topics": topics,
        "documents": documents,
    }


def build_map(
    graph: dict[str, Any], catalog: dict[str, Any], community_limit: int = 120
) -> dict[str, Any]:
    doc_by_file = {doc["file"]: doc for doc in catalog["documents"]}
    doc_group = {name: doc["group"] for name, doc in doc_by_file.items()}
    degree = collections.Counter()
    for edge in graph.get("links", []):
        degree[str(edge["source"])] += 1
        degree[str(edge["target"])] += 1
    communities = collections.defaultdict(list)
    for node in graph["nodes"]:
        if node.get("community") is not None:
            communities[node["community"]].append(node)

    def domain_for(nodes: list[dict[str, Any]]) -> str:
        counts = collections.Counter(
            doc_group.get(Path(node.get("source_file") or "").name, "其他")
            for node in nodes
        )
        if counts["其他"] == len(nodes):
            return "其他"
        return max(
            (count, domain) for domain, count in counts.items() if domain != "其他"
        )[1]

    ranked = sorted(
        communities,
        key=lambda community_id: (
            len(communities[community_id]),
            -int(community_id),
        ),
        reverse=True,
    )
    selected = ranked[:community_limit]
    selected_set = set(selected)
    node_community = {str(node["id"]): node.get("community") for node in graph["nodes"]}
    meta_edges = collections.Counter()
    for edge in graph.get("links", []):
        source = node_community.get(str(edge["source"]))
        target = node_community.get(str(edge["target"]))
        if source in selected_set and target in selected_set and source != target:
            meta_edges[tuple(sorted((source, target)))] += 1
    meta_nodes = []
    details = {}
    for community_id in selected:
        members = communities[community_id]
        name = collections.Counter(
            node.get("community_name") or f"主题 {community_id}" for node in members
        ).most_common(1)[0][0]
        ordered = sorted(
            members,
            key=lambda node: (degree[str(node["id"])], node.get("label") or ""),
            reverse=True,
        )
        meta_nodes.append(
            {
                "id": str(community_id),
                "label": name,
                "count": len(members),
                "domain": domain_for(members),
                "samples": [
                    node.get("label") for node in ordered[:5] if node.get("label")
                ],
            }
        )
        visible = ordered[:160]
        visible_ids = {str(node["id"]) for node in visible}
        detail_nodes = []
        documents = {}
        for node in visible:
            source = Path(node.get("source_file") or "").name
            if source in doc_by_file:
                doc = doc_by_file[source]
                documents[source] = {
                    "id": doc["id"],
                    "title": doc["title"],
                    "file": source,
                    "date": doc["date"],
                    "group": doc["group"],
                }
            detail_nodes.append(
                {
                    "id": str(node["id"]),
                    "label": node.get("label") or str(node["id"]),
                    "degree": degree[str(node["id"])],
                    "file_type": node.get("file_type") or "concept",
                    "source_file": source,
                    "source_url": node.get("source_url") or "",
                }
            )
        detail_edges = []
        for edge in graph.get("links", []):
            source = str(edge["source"])
            target = str(edge["target"])
            if source in visible_ids and target in visible_ids:
                detail_edges.append(
                    {
                        "source": source,
                        "target": target,
                        "relation": edge.get("relation") or "related",
                        "weight": edge.get("confidence_score")
                        or edge.get("weight")
                        or 0.5,
                    }
                )
        details[str(community_id)] = {
            "nodes": detail_nodes,
            "edges": detail_edges,
            "documents": list(documents.values()),
        }
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stats": {
            "rawNodes": len(graph["nodes"]),
            "rawEdges": len(graph.get("links", [])),
            "totalCommunities": len(communities),
            "featuredCommunities": len(selected),
        },
        "nodes": meta_nodes,
        "edges": [
            {"source": str(source), "target": str(target), "weight": weight}
            for (source, target), weight in meta_edges.items()
        ],
        "details": details,
    }


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, required=True)
    parser.add_argument("--graph", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--book-name", default="全球资金平台产品")
    parser.add_argument(
        "--book-url", default="https://yuque.antfin.com/global-fund-platform/gom7xx"
    )
    args = parser.parse_args()
    graph_path = args.graph or args.input / "graphify-out" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    catalog = build_catalog(
        args.input, graph, {"name": args.book_name, "url": args.book_url}
    )
    layered = build_map(graph, catalog)
    write_snapshot(args.output / "catalog.json", catalog)
    write_snapshot(args.output / "map" / "map-data.json", layered)
    result = {
        "status": "completed",
        "documents": catalog["stats"]["documents"],
        "nodes": catalog["stats"]["nodes"],
        "edges": catalog["stats"]["edges"],
        "communities": layered["stats"]["featuredCommunities"],
        "catalog": str(args.output / "catalog.json"),
        "map": str(args.output / "map" / "map-data.json"),
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
