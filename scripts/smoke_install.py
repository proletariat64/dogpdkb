"""Verify a built wheel through installed public CLIs in a disposable environment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]

# Fake only external remote/graph processes. Comparison and image-less OCR use
# the installed public CLIs, with no replacement packages or tool runtime imports.
REMOTE_CLI = r"""
import json, pathlib, subprocess, sys
tool, *args = sys.argv[1:]
root = pathlib.Path(__file__).parent
with (root / 'commands.jsonl').open('a') as stream:
    stream.write(json.dumps([tool, *args]) + '\n')
if '--help' in args:
    print(tool); raise SystemExit(0)
if tool in ('mdcompare', 'mdocr'):
    raise SystemExit(subprocess.call([sys.executable, '-m', tool, *args]))
def flag(name): return pathlib.Path(args[args.index(name)+1])
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
if tool == 'skylark' and args[0] == 'listing':
    write(flag('--output'), {'schema':'skylark-listing-1', 'complete':True,
        'book':{'book_id':9, 'url':'https://example.invalid/team/book', 'items_count':2},
        'documents':[{'id':i, 'document_id':str(i), 'title':f'Source {i}',
            'version':'2026-11-12T01:00:00Z', 'content_updated_at':'2026-11-12T01:00:00Z',
            'format':'md'} for i in (1,2)], 'toc':[]})
elif tool == 'skylark' and args[0] == 'export':
    rows=[]
    for doc in json.loads(flag('--request').read_text())['documents']:
        path=pathlib.Path(doc['path']); path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(f"# Source {doc['document_id']}\n\nComplete retained knowledge.\n")
        rows.append({**doc, 'status':'completed', 'version':'2026-11-12T01:00:00Z',
            'metadata':{'title':f"Source {doc['document_id']}"}})
    write(flag('--output'), {'schema':'skylark-export-result-1','status':'completed',
        'documents':rows,'counts':{'total':len(rows),'completed':len(rows),'failed':0}})
elif tool == 'graphify' and args[0] == 'source-inventory':
    write(flag('--output'), {'schema':'graphify-source-inventory-1.0','sources':[]})
elif tool == 'graphify' and args[0] == 'update-sources':
    rows=json.loads(flag('--request').read_text())['sources']
    graph=flag('--graph-dir')/'graph.json'
    write(graph, {'nodes':[], 'links':[], 'smoke_sources':[row['source_id'] for row in rows]})
    write(flag('--output'), {'schema':'graphify-source-result-1.0','status':'completed',
        'graph':str(graph),'sources':[{**row,'status':'completed'} for row in rows],
        'counts':{'total':len(rows),'completed':len(rows),'failed':0}})
else:
    raise SystemExit('unexpected public command')
"""


def inspect_artifacts(wheel: Path, sdist: Path) -> None:
    required = {
        "dogpdkb/cli.py",
        "dogpdkb/settings.py",
        "dogpdkb/records.py",
        "dogpdkb/discover.py",
        "dogpdkb/apply.py",
        "dogpdkb/graph.py",
        "dogpdkb/observe.py",
        "dogpdkb/processes.py",
        "dogpdkb/lifecycle.py",
        "dogpdkb/resources/dogpdkb.toml",
        "dogpdkb/resources/books.json",
        "mdcompare/cli.py",
        "mdcompare/catalog.py",
        "mdocr/cli.py",
        "mdocr/batch.py",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    with tarfile.open(sdist) as archive:
        source_names = {name.split("/", 1)[-1] for name in archive.getnames()}
    if (
        not required <= names
        or not {f"src/{name}" for name in required} <= source_names
    ):
        raise RuntimeError("distribution lacks the new CLI runtime")
    forbidden = {
        "knowledge_execute.py",
        "knowledge_capture.py",
        "sync_wash.py",
        "unified_sync.py",
        "get_sync_updates.py",
        "graph_sync.py",
    }
    for name in names | source_names:
        if Path(name).name in forbidden:
            raise RuntimeError(f"obsolete runtime included: {name}")
        if any(
            part in {".env", "config.local.toml", "graphify_flatten_ready", "snapshot"}
            for part in Path(name).parts
        ):
            raise RuntimeError("distribution includes machine-local configuration/data")


def smoke(dist: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for isolated installation validation")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    wheel = dist / f"dogpdkb-{version}-py3-none-any.whl"
    inspect_artifacts(wheel, dist / f"dogpdkb-{version}.tar.gz")
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
            "UTOO_PROXY_BIN",
        }
    }
    env["UV_OFFLINE"] = os.environ.get("UV_OFFLINE", "1")
    env["UV_PYTHON_DOWNLOADS"] = "never"
    with tempfile.TemporaryDirectory(prefix="dogpdkb-installed-cli-") as temporary:
        work = Path(temporary)
        env["XDG_STATE_HOME"] = str(work / "user-state")

        def run(args, expected=0):
            result = subprocess.run(
                list(map(str, args)),
                cwd=work,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
            if result.returncode != expected:
                raise RuntimeError(
                    f"installed smoke failed: {args[:2]} ({result.returncode})\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            return result.stdout

        environment = work / "env"
        run(
            [
                uv,
                "venv",
                environment,
                "--python",
                sys.executable,
                "--no-python-downloads",
            ]
        )
        python = environment / "bin/python"
        run([uv, "--no-config", "pip", "install", "--python", python, wheel])
        command = environment / "bin/dogpdkb"
        run(
            [
                python,
                "-I",
                "-c",
                (
                    "import dogpdkb,pathlib,sys; "
                    "assert pathlib.Path(dogpdkb.__file__).is_relative_to(sys.prefix)"
                ),
            ]
        )
        run([command, "--version"])
        run([command, "doctor", "--package"])
        run([command, "init-config", "-o", work / "portable"])
        assert not json.loads((work / "portable/books.json").read_text())[0]["enabled"]
        for name in (
            "discover",
            "apply",
            "sync",
            "update-graph",
            "status",
            "publish-web",
        ):
            run([command, name, "--help"])
        fixture = work / "remote.py"
        fixture.write_text(REMOTE_CLI)
        (work / "books.json").write_text(
            json.dumps(
                [
                    {
                        "key": "funds",
                        "name": "Funds",
                        "url": "https://example.invalid/team/book",
                        "corpus": "corpus",
                    }
                ]
            )
        )
        config = work / "dogpdkb.toml"
        commands = "\n".join(
            f"{name} = {json.dumps([str(python), str(fixture), name])}"
            for name in ("skylark", "graphify", "mdcompare", "mdocr")
        )
        config.write_text(
            '[source]\nbooks_file="books.json"\n'
            '[paths]\nstate="state"\nstaging="runs"\nocr_cache="ocr-cache"\n'
            "[tools]\n" + commands + "\n"
        )
        status = json.loads(run([command, "status", "--config", config, "--json"]))
        assert status["status"] == "no-runs"
        run([command, "init-state", "--config", config])
        run([command, "sync", "--config", config, "--stop-after", "plan"])
        records = list((work / "runs/funds").glob("run-*/run.json"))
        assert len(records) == 1
        assert not list((work / "corpus").glob("*.md"))
        calls = [
            json.loads(line)
            for line in (work / "commands.jsonl").read_text().splitlines()
        ]
        assert not any(row[0] in {"mdocr", "graphify"} for row in calls)
        run([command, "sync", "--config", config, "--resume", records[0].parent])
        assert len(list((work / "corpus").glob("*.md"))) == 2
        assert (work / "corpus/graphify-out/graph.json").is_file()
        status = json.loads(run([command, "status", "--config", config, "--json"]))
        assert status["status"] == "completed", status
        calls = [
            json.loads(line)
            for line in (work / "commands.jsonl").read_text().splitlines()
        ]
        phases = [row[0] for row in calls]
        assert max(
            i for i, name in enumerate(phases) if name == "mdcompare"
        ) < phases.index("mdocr")
        assert phases.index("mdocr") < next(
            i
            for i, row in enumerate(calls)
            if row[:2] == ["graphify", "update-sources"]
        )
        print(
            "Installed CLI smoke passed: isolated wheel, plan/resume, real mdcompare/mdocr, external Skylark/Graphify fixtures."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        smoke(args.dist.resolve())
    except (OSError, ValueError, RuntimeError, AssertionError) as error:
        print(f"smoke: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
