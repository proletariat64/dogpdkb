"""Install the built platform in a disposable venv, never using the source checkout."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

if __package__:
    from .process import run as run_process
else:
    from process import run as run_process

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (
    "get_sync_updates.py",
    "apply_sync_updates.py",
    "sync_formats.py",
    "graph_sync.py",
    "update_graph.py",
    "publish_web.py",
    "sync_knowledge_base.py",
)


def parse_json(content: str) -> Any:
    try:
        return json.loads(content)
    except ValueError as error:
        raise RuntimeError("invalid JSON in installation smoke output") from error


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def install_knowledge_graphify_stub(graph_env: Path) -> None:
    """Install a deterministic external Graphify boundary in the disposable venv."""
    site = (
        graph_env
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    (site / "networkx.py").write_text(
        "class Graph:\n"
        "    def add_node(self, *args, **kwargs): pass\n"
        "    def add_edge(self, *args, **kwargs): pass\n"
        "class DiGraph(Graph): pass\n"
    )
    package = site / "graphify"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("")
    (package / "cache.py").write_text(
        """import json
from pathlib import Path

def _entry(path, cache_root):
    return Path(cache_root) / "graphify-out" / "cache" / "semantic-stub" / (Path(path).name + ".json")

def check_semantic_cache(files, root, cache_root, prompt):
    nodes, edges, hyperedges, missing = [], [], [], []
    for name in files:
        entry = _entry(name, cache_root)
        value = json.loads(entry.read_text()) if entry.exists() else None
        if value is None or value["body"] != Path(name).read_text():
            missing.append(name)
            continue
        nodes.extend(value["nodes"])
        edges.extend(value["edges"])
        hyperedges.extend(value["hyperedges"])
    return nodes, edges, hyperedges, missing
"""
    )
    (package / "llm.py").write_text(
        """import json
from pathlib import Path
from graphify.cache import _entry

def detect_backend():
    return "stub"

def _default_model_for_backend(backend):
    return "stub-model"

def _extraction_system(deep=False):
    return "stub-prompt"

def extract_corpus_parallel(files, backend, root, cache_root, **kwargs):
    nodes, edges = [], []
    for path in files:
        body = path.read_text()
        source = path.name
        current_nodes = [
            {"id": "refund-approval", "label": "Refund approval", "rationale": "Refund requires approval.", "source_file": source},
            {"id": "refund-limit", "label": "Refund limit", "rationale": "Refund requires manager approval when amount exceeds 100 USD.", "source_file": source},
        ]
        current_edges = [{"source": "refund-limit", "target": "refund-approval", "relation": "requires", "rationale": "manager approval above 100 USD", "source_file": source}]
        value = {"body": body, "nodes": current_nodes, "edges": current_edges, "hyperedges": []}
        entry = _entry(path, cache_root)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(value))
        nodes.extend(current_nodes)
        edges.extend(current_edges)
    return {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 123, "output_tokens": 45, "external_calls": len(files)}
"""
    )
    (package / "detect.py").write_text(
        """import json
from pathlib import Path
def save_manifest(files, *, manifest_path, kind, root):
    path = Path(manifest_path)
    value = json.loads(path.read_text())
    for name in files["document"]:
        value[Path(name).name] = {"kind": kind}
    path.write_text(json.dumps(value))
"""
    )
    (package / "analyze.py").write_text(
        "def god_nodes(graph):\n    return []\n"
        "def suggest_questions(graph, communities, labels):\n    return []\n"
        "def surprising_connections(graph, communities):\n    return []\n"
    )
    (package / "cluster.py").write_text(
        "def score_all(graph, communities):\n    return {}\n"
        "def community_member_sigs(communities):\n    return ''\n"
    )
    (package / "report.py").write_text(
        "def generate(*args, **kwargs):\n    return 'bounded stub report'\n"
    )


def inspect_artifacts(wheel: Path, sdist: Path) -> None:
    required = {
        "dogpdkb/cli.py",
        "dogpdkb/doctor.py",
        "dogpdkb/lifecycle.py",
        "dogpdkb/instance_lock.py",
        "dogpdkb/sync_state.py",
        "dogpdkb/sync_time.py",
        "dogpdkb/sync_progress.py",
        "dogpdkb/knowledge_state.py",
        "mdocr/cli.py",
        "dogpdkb/resources/dogpdkb.toml",
        "dogpdkb/resources/books.json",
        *(f"dogpdkb/workflows/{name}" for name in RUNTIME),
    }
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist) as archive:
        source_names = {name.split("/", 1)[-1] for name in archive.getnames()}
    comparator = {
        "mdcompare/__init__.py",
        "mdcompare/__main__.py",
        "mdcompare/cli.py",
        "mdcompare/knowledge.py",
        "mdcompare/knowledge_units.py",
    }
    if (
        not comparator <= wheel_names
        or not {f"src/{name}" for name in comparator} <= source_names
    ):
        raise RuntimeError("distribution is missing mdcompare modules")
    knowledge = {
        "dogpdkb/knowledge_capture.py",
        "dogpdkb/knowledge_execute.py",
        "dogpdkb/knowledge_plan.py",
        "dogpdkb/knowledge_sync.py",
    }
    if (
        not knowledge <= wheel_names
        or not {f"src/{name}" for name in knowledge} <= source_names
    ):
        raise RuntimeError("distribution is missing knowledge workflow modules")
    if (
        not required <= wheel_names
        or not {f"src/{name}" for name in required} <= source_names
    ):
        raise RuntimeError(
            "distribution is missing platform modules or configuration templates"
        )
    for name in wheel_names | source_names:
        parts = Path(name).parts
        if any(
            part in {".env", "config.local.toml", "graphify_flatten_ready", "snapshot"}
            for part in parts
        ) or name in {"default.toml", "skylark_books/roots.json"}:
            raise RuntimeError(
                "distribution contains local configuration or corpus artifacts"
            )


#: A deterministic local Yuque boundary (JSON-RPC over stdin/stdout). It serves
#: frozen documents/details so the installed unified sync runs fully offline.
_REMOTE_PROXY = """import json
import sys
from pathlib import Path

fixture = json.loads(Path(__file__).with_name("remote.json").read_text())
for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    if request_id is None:
        continue
    if request.get("method") == "initialize":
        result = {}
    else:
        name = request["params"]["name"]
        args = request["params"]["arguments"]
        if name == "skylark_resolve_url":
            data = {"type": "book", "id": 123, "slug": "book", "name": "Funds"}
        elif name == "skylark_book_detail":
            data = {"name": "Funds", "items_count": len(fixture["documents"])}
        elif name == "skylark_doc_list":
            offset = args["offset"]
            data = fixture["documents"][offset:offset + args["limit"]]
        elif name == "skylark_book_toc":
            data = [
                {"type": "DOC", "doc_id": doc["id"], "title": doc["title"], "children": []}
                for doc in fixture["documents"]
            ]
        elif name == "skylark_doc_detail":
            data = fixture["details"][str(args["doc_id"])]
        else:
            raise AssertionError(name)
        result = {"content": [{"text": json.dumps({"ok": True, "data": data})}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
"""


def write_unified_remote(
    root: Path, documents: list[dict], bodies: dict[int, str]
) -> None:
    """Freeze a local Yuque adapter serving real-shaped documents offline."""
    write_json(
        root / "remote.json",
        {
            "documents": documents,
            "details": {
                str(doc["id"]): {
                    "id": doc["id"],
                    "body": bodies[doc["id"]],
                    "content_updated_at": doc["content_updated_at"],
                }
                for doc in documents
            },
        },
    )
    proxy = root / "fake-proxy.py"
    proxy.write_text(f"#!{sys.executable}\n" + _REMOTE_PROXY, encoding="utf-8")
    proxy.chmod(0o755)


def smoke_unified_sync(work: Path, command: Path, run, graph_env: Path) -> None:
    """Exercise the installed unified five-stage sync entry offline (ticket 08).

    This replaces the superseded human-approval / semantic-quality gate exercise:
    the unified runner is the single default ``dogpdkb sync`` entry. A changed
    source compiles through the deterministic Graphify stub in ``graph_env``; an
    unchanged second batch converges by reuse with no repeat extraction. No real
    Yuque / OCR / Graphify / network / paid call is made.
    """
    root = work / "unified-sync"
    corpus = root / "corpus/fund"
    graph_dir = corpus / "graphify-out"
    corpus.mkdir(parents=True)
    name = "Limits-2026-11-09-[1].md"
    old_body = "# Limits\n\nRefund requires approval.\n"
    new_body = (
        old_body + "\nRefund requires manager approval when amount exceeds 100 USD.\n"
    )
    (corpus / name).write_text(old_body, encoding="utf-8")
    write_json(
        graph_dir / "graph.json",
        {"nodes": [{"id": "old", "source_file": name}], "links": []},
    )
    write_json(
        root / "books.json",
        [{"key": "fund", "url": "https://example.invalid/team/book", "name": "Funds"}],
    )
    version = "2026-11-10T02:30:00+08:00"
    document = {
        "id": 1,
        "title": "Limits",
        "slug": "limits",
        "content_updated_at": version,
    }
    write_unified_remote(root, [document], {1: new_body})
    config = root / "config.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n'
        '[paths]\ncorpus="corpus"\nstaging="wash"\nstate="state"\nweb="web"\n'
        f'graphify_python="{graph_env / "bin/python"}"\n'
        '[skylark]\nmcp_url="http://127.0.0.1:9/mcp"\n'
        f'proxy_bin="{root / "fake-proxy.py"}"\n'
        "timeout_seconds=5\nmax_retries=0\nconcurrency=1\n",
        encoding="utf-8",
    )
    run([str(command), "init-state", "--config", str(config), "--book", "fund"])
    sync = [str(command), "sync", "--config", str(config), "--book", "fund"]
    first = parse_json(run(sync).strip().splitlines()[-1])
    assert first["status"] == "completed", first
    assert first["stages"]["compile"] == "completed"
    order = ("capture", "mdcompare", "plan", "ocr", "compile")
    run1 = max((root / "wash/fund").glob("run-*"))
    states = parse_json((run1 / "run.json").read_text())["stage_states"]
    assert all(states[s]["status"] in {"completed", "skipped"} for s in order)
    assert all(
        states[a]["ended_at"] <= states[b]["started_at"]
        for a, b in itertools.pairwise(order)
    )
    assert all((run1 / s / "result.json").is_file() for s in order)
    graph = parse_json((graph_dir / "graph.json").read_text())
    sources = {node["source_file"] for node in graph["nodes"]}
    assert "source-[1].md" in sources and name not in sources
    state = parse_json((corpus / ".sync-state.json").read_text())
    row = state["books"]["fund"]["documents"]["1"]
    assert row["graph"]["status"] == "covered"
    assert row["graph"]["version"]["utc"] == version

    # An unchanged remote converges to no-work: the settled source is not
    # re-selected, so nothing is re-downloaded and nothing recompiles.
    second = parse_json(run(sync).strip().splitlines()[-1])
    assert second["status"] == "completed", second
    assert second["stages"]["compile"] == "skipped"
    run2 = max((root / "wash/fund").glob("run-*"))
    assert run2 != run1
    capture2 = parse_json((run2 / "capture/result.json").read_text())["counts"]
    assert capture2["total"] == 0 and capture2["reused"] == 0
    assert parse_json((run2 / "plan/plan.json").read_text())["compile"] == []
    assert parse_json((run2 / "compile/result.json").read_text())["status"] == "skipped"


def smoke_canonical_sync(work: Path, command: Path, run, env: dict) -> None:
    """Exercise initialized historical state and precise same-day observation offline."""
    root = work / "canonical-sync"
    corpus = root / "corpus/fund"
    graph = corpus / "graphify-out/graph.json"
    name = "Limits-2026-11-10-[1].md"
    corpus.mkdir(parents=True)
    body = "# Limits\n\nRefund requires approval.\n"
    (corpus / name).write_text(body, encoding="utf-8")
    (corpus / "Local-Notes.md").write_text("Local notes without remote identity.\n")
    write_json(graph, {"nodes": [{"id": "old", "source_file": name}], "links": []})
    write_json(
        root / "books.json",
        [{"key": "fund", "url": "https://example.invalid/team/book", "name": "Funds"}],
    )
    config = root / "config.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\ncorpus="corpus"\n[skylark]\nmcp_url="http://127.0.0.1:9/mcp"\n'
    )
    before_graph = graph.read_bytes()
    initialize = [str(command), "init-state", "--config", str(config), "--book", "fund"]
    lock = Path(env["XDG_STATE_HOME"]) / "dogpdkb/sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        run(initialize, expected=1)
        assert not (corpus / ".sync-state.json").exists()
    initialized = parse_json(run(initialize))
    assert initialized["counts"]["documents"] == 1
    assert initialized["counts"]["files"] == 2
    assert initialized["counts"]["unmatched_files"] == 1
    version = "2026-11-09T18:30:00Z"
    document = {
        "id": 1,
        "title": "Limits",
        "slug": "limits",
        "content_updated_at": version,
    }
    listing = root / "listing.json"
    write_json(
        listing,
        {
            "book": {"book_id": 123, "slug": "book", "name": "Funds", "items_count": 1},
            "listing": {
                "complete": True,
                "document_count": 1,
                "pages": [{"offset": 0, "limit": 50, "item_count": 1}],
            },
            "documents": [document],
        },
    )
    details = root / "details"
    write_json(details / "1.json", {**document, "body": body})
    snapshot = root / "snapshot.json"
    run(
        [
            str(command),
            "discover",
            "--config",
            str(config),
            "--book",
            "fund",
            "--snapshot",
            str(snapshot),
            "--staging",
            str(root / "capture"),
            "--listing",
            str(listing),
            "--details-dir",
            str(details),
        ]
    )
    observed = parse_json(snapshot.read_text())
    assert observed["selected"] == [1]
    assert observed["observation"]["timezone"] == "UTC+8"
    state = parse_json((corpus / ".sync-state.json").read_text())
    row = state["books"]["fund"]["documents"]["1"]
    assert row["baseline"]["content_updated_at"] is None
    downloaded = row["steps"]["download"]
    assert downloaded["status"] == "completed"
    assert downloaded["version"]["utc"] == "2026-11-10T02:30:00+08:00"
    # discover is an independent retained tool: it observes and exports raw
    # without advancing the canonical graph or rewriting the corpus body. The
    # unified five-stage same-day comparison/reuse is exercised separately by
    # smoke_unified_sync; the unified sync is self-contained and does not consume
    # a discover claim, so the two independent flows are not mixed on one state.
    assert state["books"]["fund"]["unmatched_files"] == ["Local-Notes.md"]
    assert graph.read_bytes() == before_graph
    assert (corpus / name).read_text() == body


def smoke_managed_install(work: Path, wheel: Path, version: str, env: dict) -> None:
    """Exercise the real installer and installed uninstall without remote releases."""
    home = work / "managed-home"
    prefix = home / ".local/share/dogpdkb"
    config = home / ".config/dogpdkb"
    binary = home / ".local/bin/dogpdkb"
    tools = work / "managed-tools"
    tools.mkdir()
    # Only wheel download is replaced. uv, the wheel, Bash, doctor and CLI are real.
    curl = tools / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        f"assert {wheel.name!r} in ' '.join(args)\n"
        f"shutil.copyfile({str(wheel)!r}, args[args.index('--output') + 1])\n"
    )
    curl.chmod(0o755)
    body = (ROOT / "scripts/install.sh").read_text()
    for token, value in {
        "VERSION": version,
        "RELEASE_BASE_URL": f"https://code.alipay.com/pd.pt/dogpdkb/releases/download/v{version}",
        "WHEEL": wheel.name,
        "WHEEL_SHA256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }.items():
        body = body.replace(f"@{token}@", value)
    installer = work / "managed-install.sh"
    installer.write_text(body)
    managed_env = {
        **env,
        "HOME": str(home),
        "PATH": f"{tools}:{env.get('PATH', '')}",
        "DOGPDKB_PYTHON": sys.executable,
        "DOGPDKB_PYPI_INDEX": env["UV_DEFAULT_INDEX"],
    }

    def run(*args):
        run_process(
            args,
            cwd=work,
            env=managed_env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )

    run("bash", str(installer))
    old = (prefix / "current").resolve()
    settings = config / "dogpdkb.toml"
    settings.write_text("# user settings\n")
    data = prefix / "user-data.txt"
    data.write_text("user data\n")
    # Exercise the installed CLI update with only installer transport replaced.
    run(
        str(old / "bin/python"),
        "-I",
        "-c",
        "from pathlib import Path; from dogpdkb import lifecycle,cli; "
        f"lifecycle.download_installer = lambda version: Path({str(installer)!r}).read_bytes(); "
        f"raise SystemExit(cli.main(['update', '--version', {version!r}]))",
    )
    active = (prefix / "current").resolve()
    assert active != old and not old.exists()
    assert settings.read_text() == "# user settings\n"
    run(str(binary), "uninstall", "--yes")
    assert not active.exists()
    assert not binary.is_symlink()
    assert not (binary.parent / "mdocr").is_symlink()
    assert settings.read_text() == "# user settings\n"
    assert data.read_text() == "user data\n"


def smoke(dist: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("smoke requires uv; run make bootstrap first")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    wheel = dist / f"dogpdkb-{version}-py3-none-any.whl"
    sdist = dist / f"dogpdkb-{version}.tar.gz"
    inspect_artifacts(wheel, sdist)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DOGPDKB_", "SKYLARK_"))
        and key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "PADDLEOCR_TOKEN",
            "GRAPHIFY_PYTHON",
            "UTOO_PROXY_BIN",
        }
    }
    # Resolve before changing HOME; all installation passes share the real cache.
    env["UV_CACHE_DIR"] = str(
        Path(
            subprocess.check_output([uv, "cache", "dir"], text=True, env=env).strip()
        ).resolve()
    )
    env["UV_DEFAULT_INDEX"] = os.environ.get(
        "DOGPDKB_PYPI_INDEX",
        os.environ.get("UV_DEFAULT_INDEX", "https://pypi.org/simple"),
    )
    env["UV_OFFLINE"] = os.environ.get("UV_OFFLINE", "1")
    env["UV_PYTHON_DOWNLOADS"] = "never"
    with tempfile.TemporaryDirectory(prefix="dogpdkb-install-") as temporary:
        work = Path(temporary)
        environment = work / "env"
        python = environment / "bin/python"
        env["XDG_STATE_HOME"] = str(work / "user-state")
        command = environment / "bin/dogpdkb"
        mdocr = environment / "bin/mdocr"
        mdcompare = environment / "bin/mdcompare"

        def run(arguments: list[str], *, expected=0, cwd=work) -> str:
            result = run_process(
                arguments,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
                check=False,
            )
            if result.returncode != expected:
                raise RuntimeError(
                    f"smoke command failed ({result.returncode}): {arguments[0:2]}\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            return result.stdout

        constraints = work / "constraints.txt"
        constraints.write_text(
            run(
                [
                    uv,
                    "export",
                    "--locked",
                    "--no-dev",
                    "--no-emit-project",
                    "--no-hashes",
                    "--no-annotate",
                ],
                cwd=ROOT,
            ),
            encoding="utf-8",
        )
        env["UV_CONSTRAINT"] = str(constraints)
        run(
            [
                uv,
                "venv",
                str(environment),
                "--python",
                sys.executable,
                "--no-python-downloads",
            ]
        )
        run(
            [
                uv,
                "--no-config",
                "pip",
                "install",
                "--python",
                str(python),
                "--constraint",
                str(constraints),
                str(wheel),
                "--default-index",
                env["UV_DEFAULT_INDEX"],
            ]
        )
        run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import pathlib,sys,dogpdkb,mdocr,mdcompare; "
                    "assert pathlib.Path(dogpdkb.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix)); "
                    "assert pathlib.Path(mdocr.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix)); "
                    "assert pathlib.Path(mdcompare.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix))"
                ),
            ]
        )
        assert run([str(command), "--version"]).strip() == f"dogpdkb {version}"
        assert run([str(mdocr), "--version"]).strip() == f"mdocr {version}"
        for subcommand in (
            "sync",
            "discover",
            "apply",
            "update-graph",
            "publish-web",
            "doctor",
            "init-state",
            "update",
            "uninstall",
        ):
            run([str(command), subcommand, "--help"])
        run([str(mdocr), "--help"])
        comparator_version = run(
            [str(python), "-I", "-c", "import mdcompare; print(mdcompare.__version__)"]
        ).strip()
        assert run([str(mdcompare), "--version"]).strip() == comparator_version
        run([str(mdcompare), "compare", "--help"])
        (work / "old.md").write_text("# Limit\n100k\n", encoding="utf-8")
        (work / "new.md").write_text("# Limit\n300k\n", encoding="utf-8")
        for name, content in (("baseline", "old.md"), ("incoming", "new.md")):
            (work / f"{name}.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "documents": [
                            {
                                "knowledge_base": "installation-smoke",
                                "document_id": "1",
                                "content_kind": "raw",
                                "path": content,
                                "metadata": {
                                    "name": "Limit",
                                    "logical_path": "",
                                    "source_ref": "installation-smoke:1",
                                },
                                "resources": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        run(
            [
                str(mdcompare),
                "compare",
                "--baseline",
                "baseline.json",
                "--incoming",
                "incoming.json",
                "--output",
                "comparison.json",
                "--include-evidence",
            ]
        )
        comparison = parse_json((work / "comparison.json").read_text())
        assert comparison["summary"]["total"] == 1
        assert comparison["results"][0]["action"] == "UPDATE_TEXT_REUSE_OCR"
        assert comparison["results"][0]["text"]["byte_equal"] is False
        assert "300k" in json.dumps(comparison["results"][0]["text"]["differences"])
        assert (work / "old.md").read_text() == "# Limit\n100k\n"
        assert (work / "new.md").read_text() == "# Limit\n300k\n"
        run([str(mdcompare), "knowledge", "--help"])
        (work / "old.md").write_text(
            "# Limit\n\n- 单笔限额10万元。\n![old](missing-old.png)\n", encoding="utf-8"
        )
        (work / "new.md").write_text(
            "# Limit\n\n- 单笔限额100000元。\n![new](missing-new.png)\n- 每日结算。\n",
            encoding="utf-8",
        )
        run(
            [
                str(mdcompare),
                "knowledge",
                "--baseline",
                "baseline.json",
                "--incoming",
                "incoming.json",
                "--output",
                "knowledge.json",
            ]
        )
        knowledge = parse_json((work / "knowledge.json").read_text())
        assert knowledge["scope"] == "body_only"
        assert knowledge["results"][0]["status"] == "DELTA"
        assert knowledge["results"][0]["counts"]["reuse"] >= 1
        assert knowledge["results"][0]["counts"]["add"] == 1
        assert "media_pending" not in knowledge["results"][0]
        # A plain wheel/venv is not owned by the shell installer.
        run([str(command), "uninstall", "--yes"], expected=1)
        run([str(command), "update", "--version", version], expected=1)
        assert command.is_file()
        # The configured graph interpreter can be separate from the platform venv.
        graph_env = work / "graph-env"
        run(
            [
                uv,
                "venv",
                str(graph_env),
                "--python",
                sys.executable,
                "--no-python-downloads",
            ]
        )
        graph_script = run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import pathlib,dogpdkb; "
                    "print(pathlib.Path(dogpdkb.__file__).parent / 'workflows/update_graph.py')"
                ),
            ]
        ).strip()
        run([str(graph_env / "bin/python"), "-I", graph_script, "--help"])
        install_knowledge_graphify_stub(graph_env)

        run([str(command), "init-config"])
        knowledge_root = work / "knowledge"
        config = knowledge_root / "config.toml"
        config.parent.mkdir()
        config.write_text(
            '[skylark]\nmcp_url="http://127.0.0.1:9/mcp"\n', encoding="utf-8"
        )
        source_url = "https://example.invalid/team/book"
        old_version = "2026-11-07T00:00:00Z"
        new_version = "2026-11-09T00:00:00Z"
        new_body = (
            "# Limits\n\nRefund requires approval.\n\n"
            "Refund requires manager approval when amount exceeds 100 USD.\n"
        )

        def listing(version: str) -> dict[str, Any]:
            return {
                "book": {
                    "book_id": 123,
                    "slug": "uat",
                    "name": "UAT",
                    "items_count": 1,
                },
                "listing": {
                    "complete": True,
                    "document_count": 1,
                    "pages": [{"offset": 0, "limit": 50, "item_count": 1}],
                },
                "documents": [
                    {
                        "id": 1,
                        "title": "Limits",
                        "slug": "limits",
                        "format": "markdown",
                        "content_updated_at": version,
                    }
                ],
            }

        initial_listing = knowledge_root / "initial-listing.json"
        initial_snapshot = knowledge_root / "initial-snapshot.json"
        write_json(initial_listing, listing(old_version))
        run(
            [
                str(command),
                "discover",
                "--config",
                str(config),
                "-s",
                source_url,
                "--snapshot",
                str(initial_snapshot),
                "--staging",
                str(knowledge_root / "initial-capture"),
                "--listing",
                str(initial_listing),
            ]
        )
        initial = parse_json(initial_snapshot.read_text())
        assert initial["status"] == "initialized"
        assert initial["counts"]["selected"] == 0

        current_listing = knowledge_root / "current-listing.json"
        current_snapshot = knowledge_root / "current-snapshot.json"
        details = knowledge_root / "details"
        write_json(current_listing, listing(new_version))
        write_json(
            details / "1.json",
            {"id": 1, "body": new_body, "content_updated_at": new_version},
        )
        run(
            [
                str(command),
                "discover",
                "--config",
                str(config),
                "-s",
                source_url,
                "--snapshot",
                str(current_snapshot),
                "--baseline-snapshot",
                str(initial_snapshot),
                "--staging",
                str(knowledge_root / "current-capture"),
                "--listing",
                str(current_listing),
                "--details-dir",
                str(details),
            ]
        )
        current = parse_json(current_snapshot.read_text())
        assert current["status"] == "captured"
        assert current["changes"]["updated"] == [1]
        assert current["counts"]["captured"] == 1

        # The unified runner is the single default sync entry (ticket 08). The
        # superseded human-approval / semantic-quality daily gate is replaced by
        # an offline five-stage batch: a changed source compiles through the
        # deterministic Graphify stub, an unchanged second batch converges by
        # reuse with no repeat extraction.
        smoke_unified_sync(work, command, run, graph_env)
        report = parse_json(run([str(command), "doctor", "--package", "--json"]))
        assert report["ok"]
        run([str(command), "init-config"], expected=1)
        # Disabled template sources must not be reported as operationally ready.
        report = parse_json(run([str(command), "doctor", "--json"], expected=1))
        assert not report["ok"]
        books = parse_json((work / "books.json").read_text())
        books[0]["enabled"] = True
        (work / "books.json").write_text(json.dumps(books), encoding="utf-8")
        corpus = work / "corpus/example"
        graph_dir = corpus / "graphify-out"
        graph_dir.mkdir(parents=True)
        name = "Smoke__Document-2026-01-01-[1].md"
        (corpus / name).write_text("# Synthetic smoke document\n", encoding="utf-8")
        (graph_dir / "graph.json").write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "id": "smoke",
                            "label": "Smoke",
                            "community": 0,
                            "source_file": name,
                        }
                    ],
                    "links": [],
                }
            ),
            encoding="utf-8",
        )
        run([str(command), "publish-web", "--book", "example"])
        catalog = parse_json((work / "web/example/catalog.json").read_text())
        assert catalog["stats"]["documents"] == 1
        assert (
            parse_json((work / "web/example/map/map-data.json").read_text())["stats"][
                "rawNodes"
            ]
            == 1
        )
        run(
            [
                str(python),
                "-I",
                "-c",
                "from PIL import Image; Image.new('RGB',(2,2)).save('image.png')",
            ]
        )
        (work / "image.md").write_text(
            "# Image\n\n![local](image.png)\n", encoding="utf-8"
        )
        run([str(mdocr), "prepare", "-f", "image.md", "-o", "prepared"])
        assert (work / "prepared/image.md").is_file()
        assert list((work / "prepared/assets").rglob("*.jpg"))
        smoke_canonical_sync(work, command, run, env)
        smoke_managed_install(work, wheel, version, env)
    print(
        "smoke: PASS (wheel/sdist, CLI, mdcompare, knowledge snapshot/discover, unified five-stage sync/reuse, canonical state/UTC+8/lock, "
        "doctor, Web, images, managed upgrade/uninstall)"
    )


def main() -> int:
    def cancel(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, cancel)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        smoke(args.dist.resolve())
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"smoke: FAIL: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr or error.stdout or "", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
