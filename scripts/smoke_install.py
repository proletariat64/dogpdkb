"""Verify a built wheel through installed public CLIs in a disposable environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from subprocess import PIPE

try:
    from .process import run as run_process
except ImportError:
    from process import run as run_process

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]

HELP_OBSERVER = """import os, sys
audit_log = os.environ['DOGPDKB_HELP_AUDIT_LOG']
write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
def observe(event, args):
    if event == 'open' and args[0] == audit_log:
        return  # Only the observer's diagnostic sink may be opened for writing.
    writing = event == 'open' and (
        isinstance(args[2], int) and args[2] & write_flags
        or isinstance(args[1], str) and any(mode in args[1] for mode in 'wax+')
    )
    if writing or event.startswith('socket.') or event in {
        'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.spawn',
        'os.fork', 'os.forkpty', 'os.exec', 'pty.spawn',
        'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.link', 'os.symlink',
        'os.chmod', 'os.chown', 'os.utime', 'os.truncate', 'os.ftruncate',
        'os.setxattr', 'os.removexattr', 'fcntl.flock', 'fcntl.lockf',
    }:
        with open(audit_log, 'a') as log:
            log.write(event + '\\n')
        raise RuntimeError('help attempted a side effect: ' + event)
sys.addaudithook(observe)
"""

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
    module = 'mdocr.cli' if tool == 'mdocr' else tool
    raise SystemExit(subprocess.call([sys.executable, '-m', module, *args]))
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
elif tool == 'graphify' and args[0] == 'extract':
    graph=flag('--out')/'graphify-out/graph.json'
    write(graph, {'nodes':[], 'links':[], 'graph':{}})
    write(graph.with_name('manifest.json'), {})
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
        "mdcompare/doctor.py",
        "mdcompare/catalog.py",
        "mdcompare/pi_score.mjs",
        "mdcompare/semantic_score_format.json",
        "mdcompare/semantic_score_prompt.txt",
        "mdocr/cli.py",
        "mdocr/doctor.py",
        "mdocr/runner.py",
        "mdocr/help.py",
        "mdcompare/help.py",
        "dogpdkb/help.py",
        "dogpdkb/resources/help/configuration.txt",
        "dogpdkb/resources/help/root.txt",
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
    caller_cache = os.environ.get("UV_CACHE_DIR") or str(
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv"
    )
    env["UV_CACHE_DIR"] = str(Path(caller_cache).expanduser().resolve())
    with tempfile.TemporaryDirectory(prefix="dogpdkb-installed-cli-") as temporary:
        work = Path(temporary)
        env["XDG_STATE_HOME"] = str(work / "user-state")
        env["XDG_CONFIG_HOME"] = str(work / "user-config")
        env["XDG_CACHE_HOME"] = str(work / "user-cache")
        env["HOME"] = str(work / "home")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        def run(args, expected=0, *, extra_env=None):
            result = run_process(
                list(map(str, args)),
                cwd=work,
                env=env | (extra_env or {}),
                text=True,
                stdout=PIPE,
                stderr=PIPE,
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
        observer = work / "help-observer"
        observer.mkdir()
        (observer / "sitecustomize.py").write_text(HELP_OBSERVER, encoding="utf-8")
        audit_log = work / "help-external-activity.txt"
        help_env = {
            "PYTHONPATH": str(observer),
            "DOGPDKB_HELP_AUDIT_LOG": str(audit_log),
        }
        help_checks = 0
        before_help = {path.relative_to(work) for path in work.rglob("*")}
        for tool in ("dogpdkb", "mdcompare", "mdocr"):
            executable = environment / "bin" / tool
            root_help = run([executable, "--help"], extra_env=help_env)
            # Commands are discovered from the public root help, so a new leaf
            # cannot silently remain outside the installed acceptance matrix.
            commands = re.findall(
                r"^    ([a-z][a-z0-9-]*)\s{2,}",
                root_help.split("options:", 1)[0],
                re.MULTILINE,
            )
            assert commands, f"no public commands discoverable for {tool}"
            for scope in ([], *([name] for name in commands)):
                ordinary = run([executable, *scope, "--help"], extra_env=help_env)
                assert ordinary == run([executable, *scope, "-h"], extra_env=help_env)
                full = run([executable, *scope, "--help", "--full"], extra_env=help_env)
                assert full == run(
                    [executable, *scope, "-h", "--full"], extra_env=help_env
                )
                assert len(full) > len(ordinary), (tool, scope)
                assert "--full" in ordinary, (tool, scope)
                help_checks += 4
            run([executable, "--version"], extra_env=help_env)
            help_checks += 1
        after_help = {path.relative_to(work) for path in work.rglob("*")}
        assert not audit_log.exists(), (
            "installed help attempted filesystem, network or child-process activity"
        )
        assert before_help == after_help, "installed help created runtime files"
        print(
            f"Installed offline help: {help_checks} flag/version checks passed; "
            "no runtime writes, socket activity, or child processes."
        )
        for tool, mode in (("mdcompare", "offline"), ("mdocr", "prepare")):
            result = json.loads(
                run(
                    [environment / "bin" / tool, "doctor", "--mode", mode, "--json"],
                    extra_env=help_env,
                )
            )
            assert result["schema"] == f"{tool}-doctor-1"
            assert result["ok"] and not result["network_checked"]
        assert not audit_log.exists(), (
            "offline doctor wrote a file, used a socket or launched a process"
        )
        run([command, "doctor", "--package"])
        run([command, "init-config", "-o", work / "portable"])
        assert not json.loads((work / "portable/books.json").read_text())[0]["enabled"]
        for name in (
            "discover",
            "apply",
            "sync",
            "update-graph",
            "status",
            "purge",
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
                        "sources": [
                            {
                                "key": "team",
                                "type": "yuque",
                                "url": "https://example.invalid/team/book",
                            },
                            {"key": "inbox", "type": "directory", "path": "inbox"},
                        ],
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
        (work / "corpus").mkdir()
        (work / "inbox/2026-11-19").mkdir(parents=True)
        note = work / "inbox/2026-11-19/local.md"
        original = "# Manual settlement instructions\n\nApprove before settlement.\n"
        note.write_text(original)
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
        note.write_text("# Revised instructions\n\nReject cancelled requests.\n")
        run([command, "sync", "--config", config, "--resume", records[0].parent])
        published = list((work / "corpus").glob("*.md"))
        assert len(published) == 3
        assert any(path.read_text() == original for path in published)
        note.write_text(original)
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
            i for i, row in enumerate(calls) if row[:2] == ["graphify", "extract"]
        )
        assert records[0].is_file()
        assert not list(records[0].parent.glob("graphify-*"))
        assert not (records[0].parent / "raw").exists()
        assert list((work / "state/funds/inputs").rglob("*.md"))
        run([command, "sync", "--config", config])
        later_calls = [
            json.loads(line)
            for line in (work / "commands.jsonl").read_text().splitlines()
        ]
        for operation in (("skylark", "export"), ("graphify", "extract")):
            assert sum(row[:2] == list(operation) for row in later_calls) == sum(
                row[:2] == list(operation) for row in calls
            )
        note.write_text("# Revised instructions\n\nReject cancelled requests.\n")
        run([command, "sync", "--config", config, "--source", "inbox"])
        refreshed_calls = [
            json.loads(line)
            for line in (work / "commands.jsonl").read_text().splitlines()
        ][len(later_calls) :]
        assert ["graphify", "extract"] in (row[:2] for row in refreshed_calls)
        assert all(row[0] != "skylark" for row in refreshed_calls)
        assert any(path.read_text() == note.read_text() for path in published)
        protected = {
            path: path.read_bytes()
            for directory in (work / "state", work / "corpus", work / "ocr-cache")
            for path in directory.rglob("*")
            if path.is_file() and path.name != ".dogpdkb.lock"
        }
        run([command, "purge", "--config", config, "--all"])
        assert not list((work / "runs/funds").glob("run-*"))
        assert all(path.read_bytes() == value for path, value in protected.items())
        status = json.loads(run([command, "status", "--config", config, "--json"]))
        assert status["status"] == "no-runs", status
        paused = json.loads(
            run([command, "sync", "--config", config, "--stop-after", "plan"])
        )
        run([command, "purge", "--config", config, "--run", paused["run"]], expected=1)
        run([command, "purge", "--config", config, "--run", paused["run"], "--force"])
        print(
            "Installed CLI smoke passed: isolated wheel, mixed plan/resume, directory-only refresh, cleanup/no-op/purge, real mdcompare/mdocr, external Skylark/Graphify fixtures."
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
