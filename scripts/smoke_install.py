"""Install the built platform in a disposable venv, never using the source checkout."""

from __future__ import annotations

import argparse
import hashlib
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
        "dogpdkb/lifecycle.py",
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
        "DOGPDKB_PYPI_INDEX": "https://pypi.org/simple",
    }

    def run(*args):
        subprocess.run(
            args,
            cwd=work,
            env=managed_env,
            check=True,
            capture_output=True,
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
            "update",
            "uninstall",
        ):
            run([str(command), subcommand, "--help"])
        run([str(mdocr), "--help"])
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
        smoke_managed_install(work, wheel, version, env)
    print(
        "smoke: PASS (wheel/sdist, CLI, doctor, Web, images, managed upgrade/uninstall)"
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
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"smoke: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
