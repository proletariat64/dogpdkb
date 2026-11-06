"""Install the built platform in a disposable venv, never using the source checkout."""

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
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

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


def inspect_artifacts(wheel: Path, sdist: Path) -> None:
    required = {
        "dogpdkb/cli.py",
        "dogpdkb/doctor.py",
        "mdocr/cli.py",
        "dogpdkb/resources/dogpdkb.toml",
        "dogpdkb/resources/books.json",
        *(f"dogpdkb/workflows/{name}" for name in RUNTIME),
    }
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist) as archive:
        source_names = {name.split("/", 1)[-1] for name in archive.getnames()}
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
    with tempfile.TemporaryDirectory(prefix="dogpdkb-install-") as temporary:
        work = Path(temporary)
        environment = work / "env"
        python = environment / "bin/python"
        command = environment / "bin/dogpdkb"
        mdocr = environment / "bin/mdocr"

        def run(arguments: list[str], *, expected=0, cwd=work) -> str:
            result = subprocess.run(
                arguments,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
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
                "https://pypi.org/simple",
            ]
        )
        run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import pathlib,sys,dogpdkb,mdocr; "
                    "assert pathlib.Path(dogpdkb.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix)); "
                    "assert pathlib.Path(mdocr.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix))"
                ),
            ]
        )
        assert run([str(command), "--version"]).strip() == f"dogpdkb {version}"
        for subcommand in (
            "sync",
            "discover",
            "apply",
            "update-graph",
            "publish-web",
            "doctor",
        ):
            run([str(command), subcommand, "--help"])
        run([str(mdocr), "--help"])
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
        report = parse_json(run([str(command), "doctor", "--package", "--json"]))
        assert report["ok"]
        run([str(command), "init-config"])
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
    print(
        "smoke: PASS (wheel/sdist contents, isolated install, CLI, doctor, Web data, image prepare)"
    )


def main() -> int:
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
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"smoke: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
