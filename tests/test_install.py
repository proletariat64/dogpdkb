"""Exercise the real Bash installer offline, including failure-safe upgrades."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "scripts/install.sh"
PAYLOAD = b"offline wheel fixture"


class Installer:
    def __init__(self, root):
        self.root = root
        self.home = root / "home with spaces"
        self.tools = root / "tools"
        self.tools.mkdir()
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.tools),
            "TEST_VERSION": "0.1.0",
            "FAKE_LOG": str(root / "commands.jsonl"),
            "FAKE_WHEEL": str(root / "wheel"),
            "FAKE_UV_SOURCE": str(root / "uv-source"),
            "FAKE_CLI_SOURCE": str(root / "cli-source"),
        }
        for key in list(self.env):
            if key.startswith(("DOGPDKB_", "UV_", "FAIL_", "ANTCODE_")):
                del self.env[key]
        (root / "wheel").write_bytes(PAYLOAD)
        for name in (
            "uname",
            "sha256sum",
            "mktemp",
            "readlink",
            "mkdir",
            "rm",
            "ln",
            "mv",
            "sh",
            "bash",
            "flock",
        ):
            (self.tools / name).symlink_to(shutil.which(name))
        self.python_script(
            self.tools / "curl",
            """
import os, pathlib, sys
args = sys.argv[1:]
assert args[0] == '--disable'
assert '--proto' in args and '--proto-redir' in args
if os.environ.get('FAIL_DOWNLOAD'): sys.exit(22)
target = pathlib.Path(args[args.index('--output') + 1])
url = next(a for a in args if a.startswith('https://'))
if 'astral.sh' in url:
    target.write_text('UV_DEST="$UV_UNMANAGED_INSTALL"\\n/bin/mkdir -p "$UV_DEST"\\n/bin/cp "$FAKE_UV_SOURCE" "$UV_DEST/uv"\\n')
else:
    target.write_bytes(pathlib.Path(os.environ['FAKE_WHEEL']).read_bytes())
""",
        )
        self.python_script(
            root / "cli-source",
            """
import json, os, pathlib, sys
VERSION = '@TEST_VERSION@'
if '--version' in sys.argv:
    print(pathlib.Path(sys.argv[0]).name + ' ' + VERSION)
elif 'doctor' in sys.argv:
    if os.environ.get('FAIL_DOCTOR'): sys.exit(1)
    print('doctor: PASS')
elif 'init-config' in sys.argv:
    directory = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'dogpdkb.toml').write_text('# disabled example\\n')
    (directory / 'books.json').write_text(json.dumps([{'enabled': False}]))
""",
        )
        self.python_script(
            root / "uv-source",
            """
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[0] == 'venv':
    directory = pathlib.Path(args[-1]) / 'bin'
    directory.mkdir(parents=True)
    (directory / 'python').symlink_to(sys.executable)
    (directory.parent / 'pyvenv.cfg').write_text('home = test fixture\\n')
    body = pathlib.Path(os.environ['FAKE_CLI_SOURCE']).read_text().replace('@TEST_VERSION@', os.environ['TEST_VERSION'])
    for name in ('dogpdkb', 'mdocr', 'mdcompare'):
        path = directory / name
        path.write_text(body)
        path.chmod(0o755)
    if os.environ.get('FAIL_MDCOMPARE'): (directory / 'mdcompare').unlink()
elif args[:2] == ['pip', 'install']:
    assert '--default-index' in args
    if os.environ.get('FAIL_INSTALL'): sys.exit(1)
else:
    raise AssertionError(args)
""",
        )
        (self.tools / "uv").symlink_to(root / "uv-source")
        self.python_script(self.tools / "node", "print('v22.0.0')\n")
        self.python_script(
            self.tools / "npm",
            """
import json, os, pathlib, sys
args = sys.argv[1:]
assert args[0] == 'install' and args[-1] == 'pi-skylark@latest'
with open(os.environ['FAKE_LOG'], 'a') as stream:
    stream.write(json.dumps(['npm', *args]) + '\\n')
if os.environ.get('FAIL_NPM'): sys.exit(1)
prefix = pathlib.Path(args[args.index('--prefix') + 1])
binary = prefix / 'node_modules/.bin/skylark'
binary.parent.mkdir(parents=True)
binary.write_text(pathlib.Path(os.environ['FAKE_CLI_SOURCE']).read_text().replace('@TEST_VERSION@', os.environ.get('SKYLARK_LATEST', '1.0.0')))
binary.chmod(0o755)
""",
        )

    @staticmethod
    def python_script(path, body):
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)

    @property
    def prefix(self):
        return self.home / ".local/share/dogpdkb"

    @property
    def config(self):
        return self.home / ".config/dogpdkb"

    @property
    def binary(self):
        return self.home / ".local/bin/dogpdkb"

    def render(self, version):
        text = TEMPLATE.read_text()
        for token, value in {
            "VERSION": version,
            "RELEASE_BASE_URL": f"https://code.alipay.com/pd.pt/dogpdkb/releases/download/v{version}",
            "WHEEL": f"dogpdkb-{version}-py3-none-any.whl",
            "WHEEL_SHA256": hashlib.sha256(PAYLOAD).hexdigest(),
        }.items():
            text = text.replace(f"@{token}@", value)
        script = self.root / f"install-{version}.sh"
        script.write_text(text)
        return script

    def run(self, version="0.1.0"):
        self.env["TEST_VERSION"] = version
        return subprocess.run(
            ["/bin/bash", str(self.render(version))],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def version(self):
        return subprocess.check_output(
            [str(self.binary), "--version"], env=self.env, text=True
        ).strip()


@pytest.fixture
def installer(tmp_path):
    return Installer(tmp_path)


def test_install_and_upgrade_preserve_config_and_data(installer):
    assert installer.run().returncode == 0
    assert installer.version() == "dogpdkb 0.1.0"
    old = (installer.prefix / "current").resolve()
    assert (installer.binary.parent / "mdocr").is_symlink()
    compare = installer.binary.parent / "mdcompare"
    assert compare.is_symlink()
    assert (
        subprocess.check_output(
            [str(compare), "--version"], env=installer.env, text=True
        ).strip()
        == "mdcompare 0.1.0"
    )
    config = installer.config / "dogpdkb.toml"
    config.write_text("# user configuration\n")
    data = installer.config / "corpus" / "user.md"
    data.parent.mkdir()
    data.write_text("user data")
    assert installer.run("0.1.1").returncode == 0
    assert installer.version() == "dogpdkb 0.1.1"
    assert (
        subprocess.check_output(
            [str(compare), "--version"], env=installer.env, text=True
        ).strip()
        == "mdcompare 0.1.1"
    )
    assert (installer.prefix / "current").resolve() != old
    assert not old.exists()
    assert config.read_text() == "# user configuration\n"
    assert data.read_text() == "user data"
    previous = (installer.prefix / "current").resolve()
    assert installer.run("0.1.1").returncode == 0
    assert not previous.exists()
    assert len(list((installer.prefix / "versions").iterdir())) == 1
    assert config.read_text() == "# user configuration\n"


@pytest.mark.parametrize(
    "failure", ["DOWNLOAD", "CHECKSUM", "INSTALL", "DOCTOR", "MDCOMPARE", "NPM"]
)
def test_failed_upgrade_keeps_working_install(installer, failure):
    assert installer.run().returncode == 0
    old = (installer.prefix / "current").readlink()
    if failure == "CHECKSUM":
        (installer.root / "wheel").write_bytes(b"wrong bytes")
    else:
        installer.env[f"FAIL_{failure}"] = "1"
    assert installer.run("0.1.1").returncode != 0
    assert (installer.prefix / "current").readlink() == old
    assert installer.version() == "dogpdkb 0.1.0"
    assert list((installer.prefix / "versions").iterdir()) == [old]


def test_bootstrap_uv_without_antcode_or_existing_uv(installer):
    (installer.tools / "uv").unlink()
    assert installer.run().returncode == 0
    assert (installer.prefix / "tools/uv").is_file()
    assert installer.version() == "dogpdkb 0.1.0"


@pytest.mark.parametrize("kind", ["file", "symlink"])
@pytest.mark.parametrize("command", ["dogpdkb", "mdocr", "mdcompare", "skylark"])
def test_refuse_to_overwrite_unmanaged_binary(installer, kind, command):
    installer.binary.parent.mkdir(parents=True)
    binary = installer.binary.parent / command
    if kind == "file":
        binary.write_text("other installation")
    else:
        binary.symlink_to("/does/not/exist")
    result = installer.run()
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    assert not (installer.prefix / "current").exists()
    if kind == "file":
        assert binary.read_text() == "other installation"
    else:
        assert binary.readlink() == Path("/does/not/exist")


def test_upgrade_adds_missing_mdcompare_link(installer):
    assert installer.run().returncode == 0
    compare = installer.binary.parent / "mdcompare"
    compare.unlink(missing_ok=True)
    ((installer.prefix / "current").resolve() / "bin/mdcompare").unlink()
    assert installer.run("0.1.1").returncode == 0
    assert compare.readlink() == installer.prefix / "current/bin/mdcompare"
    env = {
        **installer.env,
        "PATH": str(compare.parent) + os.pathsep + installer.env["PATH"],
    }
    assert (
        subprocess.check_output(["mdcompare", "--version"], env=env, text=True).strip()
        == "mdcompare 0.1.1"
    )


def test_reinstall_refreshes_skylark_latest(installer):
    assert installer.run().returncode == 0
    command = installer.binary.parent / "skylark"
    assert command.readlink() == installer.prefix / "current/bin/skylark"
    assert (
        subprocess.check_output(
            [str(command), "--version"], env=installer.env, text=True
        ).strip()
        == "skylark 1.0.0"
    )
    previous = (installer.prefix / "current").resolve()
    installer.env["SKYLARK_LATEST"] = "1.1.0"
    assert installer.run().returncode == 0
    assert (
        subprocess.check_output(
            [str(command), "--version"], env=installer.env, text=True
        ).strip()
        == "skylark 1.1.0"
    )
    assert not previous.exists()


@pytest.mark.parametrize("tool", ["node", "npm"])
def test_missing_node_or_npm_fails_before_installation(installer, tool):
    (installer.tools / tool).unlink()
    result = installer.run()
    assert result.returncode == 1 and f"Missing prerequisite: {tool}" in result.stderr
    assert not installer.prefix.exists()


def test_partial_config_is_not_overwritten_or_completed(installer):
    installer.config.mkdir(parents=True)
    books = installer.config / "books.json"
    books.write_text("user books")
    assert installer.run().returncode == 0
    assert books.read_text() == "user books"
    assert not (installer.config / "dogpdkb.toml").exists()


def test_invalid_unrendered_template_fails_before_install(installer):
    result = subprocess.run(
        ["/bin/bash", str(TEMPLATE)],
        env=installer.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "published Release" in result.stderr
    assert not installer.home.exists()


def test_override_paths_and_mirror(installer):
    installer.env.update(
        DOGPDKB_HOME=str(installer.root / "custom-prefix"),
        DOGPDKB_BIN_DIR=str(installer.root / "custom-bin"),
        DOGPDKB_CONFIG_DIR=str(installer.root / "custom-config"),
        DOGPDKB_PYPI_INDEX="https://pypi.org/simple",
    )
    assert installer.run().returncode == 0
    assert (installer.root / "custom-bin/dogpdkb").is_symlink()
    assert (installer.root / "custom-bin/mdcompare").is_symlink()
    assert (installer.root / "custom-config/books.json").is_file()
    calls = [
        json.loads(line)
        for line in (installer.root / "commands.jsonl").read_text().splitlines()
    ]
    pip = next(row for row in calls if row[:2] == ["pip", "install"])
    assert pip[pip.index("--default-index") + 1] == "https://pypi.org/simple"


@pytest.mark.parametrize("damage", ["marker", "current-escape", "versions-link"])
def test_installer_rejects_unsafe_existing_environment(installer, damage):
    assert installer.run().returncode == 0
    old = (installer.prefix / "current").resolve()
    if damage == "marker":
        (old / ".dogpdkb-managed").write_text("invalid")
    elif damage == "current-escape":
        (installer.prefix / "current").unlink()
        (installer.prefix / "current").symlink_to(installer.root)
    else:
        versions = installer.prefix / "versions"
        versions.rename(installer.root / "elsewhere")
        versions.symlink_to(installer.root / "elsewhere")
    result = installer.run("0.1.1")
    assert result.returncode != 0
    assert (installer.config / "books.json").is_file()


@pytest.mark.parametrize("relative", ["versions", "versions/config", "."])
def test_installer_rejects_configuration_overlapping_disposable_environments(
    installer, relative
):
    installer.env["DOGPDKB_CONFIG_DIR"] = str(installer.prefix / relative)
    result = installer.run()
    assert result.returncode != 0
    assert "overlap" in result.stderr
    assert not (installer.prefix / "current").exists()


def test_installer_does_not_sweep_unrelated_versions(installer):
    assert installer.run().returncode == 0
    unknown = installer.prefix / "versions/9.9.9.aaaaaa"
    unknown.mkdir()
    (unknown / "data.txt").write_text("keep")
    linked = installer.prefix / "versions/8.8.8.bbbbbb"
    linked.symlink_to(installer.config)
    assert installer.run("0.1.1").returncode == 0
    assert (unknown / "data.txt").read_text() == "keep"
    assert linked.is_symlink()


def test_signal_immediately_after_activation_keeps_environment(installer):
    assert installer.run().returncode == 0
    old = (installer.prefix / "current").resolve()
    (installer.tools / "mv").unlink()
    real_mv = shutil.which("mv")
    installer.python_script(
        installer.tools / "mv",
        f"""
import os, signal, subprocess, sys
subprocess.run([{real_mv!r}, *sys.argv[1:]], check=True)
if sys.argv[-1].endswith('/current'):
    os.kill(os.getppid(), signal.SIGTERM)
""",
    )
    result = installer.run("0.1.1")
    assert result.returncode == 143
    assert (installer.prefix / "current").resolve().is_dir()
    assert installer.version() == "dogpdkb 0.1.1"
    assert old.is_dir()
    interrupted = (installer.prefix / "current").resolve()
    (installer.tools / "mv").unlink()
    (installer.tools / "mv").symlink_to(real_mv)
    assert installer.run("0.1.2").returncode == 0
    assert (installer.prefix / "current").resolve().is_dir()
    assert not interrupted.exists()


@pytest.mark.parametrize(
    "location", ["home", ".local", ".local/bin", ".config/dogpdkb"]
)
@pytest.mark.parametrize("owned_link", [True, False])
def test_legacy_default_upgrade_resolves_symlinked_paths(
    installer, location, owned_link
):
    target = installer.root / "physical-directory"
    target.mkdir()
    link = installer.home if location == "home" else installer.home / location
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    assert installer.run().returncode == 0
    old = (installer.prefix / "current").resolve()
    # v0.1.0 used this layout and canonical links, but did not write a marker.
    (old / ".dogpdkb-managed").unlink()
    config = installer.config / "dogpdkb.toml"
    config.write_text("# preserve configuration\n")
    data = installer.config / "user.md"
    data.write_text("preserve data")
    if not owned_link:
        installer.binary.unlink()
        installer.binary.write_text("unrelated command")
    result = installer.run("0.1.1")
    if owned_link:
        assert result.returncode == 0, result.stderr
        assert installer.version() == "dogpdkb 0.1.1"
        assert not old.exists()
    else:
        assert result.returncode != 0
        assert "cannot be replaced safely" in result.stderr
        assert (installer.prefix / "current").resolve() == old
        assert old.is_dir()
        assert installer.binary.read_text() == "unrelated command"
    assert link.is_symlink()
    assert config.read_text() == "# preserve configuration\n"
    assert data.read_text() == "preserve data"
