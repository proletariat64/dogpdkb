"""Responsibility guard: runtime clients may launch tools, never import them."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOREIGN = {"graphify", "mdocr", "mdcompare", "skylark"}
RUNTIME_SCRIPTS = {
    "publish_web.py",
    "smoke_install.py",
    "get_sync_updates.py",
    "apply_sync_updates.py",
    "sync_knowledge_base.py",
    "update_graph.py",
    "graph_sync.py",
    "sync_formats.py",
}


def forbidden(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name.split(".")[0] in FOREIGN:
                    yield node.lineno, f"import {name.name}"
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] in FOREIGN
        ):
            yield node.lineno, f"from {node.module}"
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if name in {"__import__", "import_module"}:
                target = (
                    node.args[0].value
                    if node.args and isinstance(node.args[0], ast.Constant)
                    else None
                )
                if not isinstance(target, str) or target.split(".")[0] in FOREIGN:
                    yield node.lineno, f"foreign or unresolved dynamic import: {target}"
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(
                r"\b(?:from|import)\s+(?:graphify|mdocr|mdcompare|skylark)(?:\b|\.)",
                node.value,
            )
        ):
            try:
                embedded = ast.parse(node.value)
            except SyntaxError:
                continue
            for line, message in forbidden(embedded):
                yield node.lineno, f"embedded Python line {line}: {message}"


def test_runtime_has_no_foreign_tool_imports_or_embedded_internal_shortcuts():
    files = list((ROOT / "src/dogpdkb").rglob("*.py"))
    files += [
        ROOT / "scripts" / name
        for name in RUNTIME_SCRIPTS
        if (ROOT / "scripts" / name).is_file()
    ]
    violations = [
        (str(path.relative_to(ROOT)), line, message)
        for path in files
        for line, message in forbidden(ast.parse(path.read_text()))
    ]
    assert violations == []


def test_only_one_runtime_pipeline_is_present():
    obsolete = {
        "knowledge_execute.py",
        "knowledge_capture.py",
        "knowledge_plan.py",
        "unified_sync.py",
        "sync_wash.py",
        "sync_anomalies.py",
        "ocr_cache.py",
        "sync_results.py",
        "sync_state.py",
    }
    assert not [
        path for path in (ROOT / "src/dogpdkb").rglob("*.py") if path.name in obsolete
    ]
