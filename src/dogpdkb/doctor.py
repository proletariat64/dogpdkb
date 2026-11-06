"""Read-only offline installation and operational preflight checks."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import mdocr
from mdocr.config import load_config, select_book

GRAPH_PROBE = """
import json, sys
sys.path.append(sys.argv[1])
import networkx
from mdocr.config import load_config
from graphify.cache import check_semantic_cache
from graphify.llm import (
    _default_model_for_backend, _extraction_system, detect_backend,
    extract_corpus_parallel,
)
from graphify.analyze import god_nodes, suggest_questions, surprising_connections
from graphify.cluster import community_member_sigs, score_all
from graphify.report import generate
from graphify.detect import save_manifest
print(json.dumps({"backend": bool(detect_backend())}))
"""


def writable_directory(path: Path) -> bool:
    """Check the nearest existing ancestor; do not create files or directories."""
    try:
        candidate = path
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)
    except OSError:
        return False


def graph_runtime(interpreter: Path) -> tuple[bool, bool]:
    """Probe the exact APIs used by graph updates, without extraction or network."""
    try:
        result = subprocess.run(
            [
                str(interpreter),
                "-B",
                "-c",
                GRAPH_PROBE,
                str(Path(mdocr.__file__).resolve().parents[1]),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            return False, False
        payload = json.loads(result.stdout)
        return True, payload.get("backend") is True
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return False, False


def diagnose(
    config_path: Path | None = None,
    book_key: str | None = None,
    package_only: bool = False,
    require_ocr: bool = False,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"check": name, "status": status, "message": message})

    add(
        "platform",
        "pass" if os.name == "posix" else "fail",
        "POSIX is required for graph publication locks",
    )
    for module in ("requests", "PIL.Image", "mdocr.cli"):
        try:
            importlib.import_module(module)
        except (ImportError, OSError):
            add(module, "fail", "runtime dependency is unavailable; reinstall dogpdkb")
        else:
            add(module, "pass", "runtime dependency imports successfully")
    from dogpdkb.cli import RESOURCES, WORKFLOWS

    root = Path(__file__).resolve().parent
    present = all(
        (root / "workflows" / name).is_file()
        for name in (
            *WORKFLOWS.values(),
            "sync_formats.py",
            "graph_sync.py",
        )
    )
    add(
        "workflows",
        "pass" if present else "fail",
        "sync, discovery, export, graph and Web runtime modules",
    )
    resources = all(
        (RESOURCES / name).is_file() for name in ("dogpdkb.toml", "books.json")
    )
    add(
        "templates", "pass" if resources else "fail", "portable configuration templates"
    )
    if require_ocr or not package_only:
        token = bool(os.environ.get("PADDLEOCR_TOKEN", "").strip())
        add(
            "ocr-token",
            "pass" if token else ("fail" if require_ocr else "warn"),
            "token present (not displayed or authenticated)"
            if token
            else "PADDLEOCR_TOKEN missing; image OCR needs it (--require-ocr makes this fatal)",
        )
    if package_only:
        return checks
    try:
        config = load_config(config_path)
    except ValueError:
        add(
            "configuration",
            "fail",
            "cannot load configuration; use init-config or select a valid --config",
        )
        return checks
    add("configuration", "pass", "TOML schema and settings are valid")
    try:
        book = select_book(config, book_key)
    except ValueError:
        add("source", "fail", "select exactly one valid enabled source with --book")
        return checks
    add("source", "pass", "source registry is valid and one enabled book is selected")
    if ".invalid" in config.mcp_url or ".invalid" in book.url:
        add("endpoint", "fail", "replace the example gateway and source URLs")
    proxy = shutil.which(config.proxy_bin)
    add(
        "proxy",
        "pass" if proxy else "fail",
        "configured proxy executable found"
        if proxy
        else "install or configure utoo-proxy",
    )
    add(
        "corpus",
        "pass" if book.corpus.is_dir() else "fail",
        "existing corpus directory is required; doctor does not create or backfill it",
    )
    graph_dir = config.graph_dir or book.corpus / "graphify-out"
    try:
        graph = json.loads((graph_dir / "graph.json").read_text(encoding="utf-8"))
        valid_graph = isinstance(graph, dict) and all(
            isinstance(graph.get(key), list) for key in ("nodes", "links")
        )
    except (OSError, ValueError):
        valid_graph = False
    add(
        "graph-baseline",
        "pass" if valid_graph else "fail",
        "existing graph.json must contain nodes/links arrays; no graph rebuild is performed",
    )
    for name, path in (
        ("corpus", book.corpus),
        ("graph", graph_dir),
        ("staging", config.staging),
        ("state", config.state),
        ("web", config.web / book.key),
    ):
        add(
            f"writable-{name}",
            "pass" if writable_directory(path) else "fail",
            "directory or nearest existing ancestor must be writable/searchable",
        )
    api, backend = graph_runtime(config.graphify_python)
    add(
        "graphify-api",
        "pass" if api else "fail",
        "configured interpreter provides the Graphify/NetworkX APIs used by this platform"
        if api
        else "configure a Graphify interpreter with compatible APIs and NetworkX",
    )
    if api:
        add(
            "graphify-backend",
            "pass" if backend else "fail",
            "LLM backend configured (not authenticated)"
            if backend
            else "configure a Graphify LLM backend before uncached graph extraction",
        )
    add(
        "authentication",
        "warn",
        "offline only: Yuque, PaddleOCR and LLM authentication/connectivity were not tested",
    )
    return checks


def print_report(checks: list[dict[str, str]], *, json_output: bool = False) -> None:
    ok = not any(row["status"] == "fail" for row in checks)
    if json_output:
        print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False))
    else:
        for row in checks:
            print(f"{row['status'].upper():4} {row['check']}: {row['message']}")
        print("doctor: PASS" if ok else "doctor: FAIL")
