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
    print('dogpdkb ' + VERSION)
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
    body = pathlib.Path(os.environ['FAKE_CLI_SOURCE']).read_text().replace('@TEST_VERSION@', os.environ['TEST_VERSION'])
    for name in ('dogpdkb', 'mdocr'):
        path = directory / name
        path.write_text(body)
        path.chmod(0o755)
elif args[:2] == ['pip', 'install']:
    assert '--default-index' in args
    if os.environ.get('FAIL_INSTALL'): sys.exit(1)
else:
    raise AssertionError(args)
""",
        )
        (self.tools / "uv").symlink_to(root / "uv-source")

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
    config = installer.config / "dogpdkb.toml"
    config.write_text("# user configuration\n")
    data = installer.config / "corpus" / "user.md"
    data.parent.mkdir()
    data.write_text("user data")
    assert installer.run("0.1.1").returncode == 0
    assert installer.version() == "dogpdkb 0.1.1"
    assert (installer.prefix / "current").resolve() != old
    assert old.is_dir()
    assert config.read_text() == "# user configuration\n"
    assert data.read_text() == "user data"
    assert installer.run("0.1.1").returncode == 0
    assert config.read_text() == "# user configuration\n"


@pytest.mark.parametrize("failure", ["DOWNLOAD", "CHECKSUM", "INSTALL", "DOCTOR"])
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
def test_refuse_to_overwrite_unmanaged_binary(installer, kind):
    installer.binary.parent.mkdir(parents=True)
    if kind == "file":
        installer.binary.write_text("other installation")
    else:
        installer.binary.symlink_to("/does/not/exist")
    result = installer.run()
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    assert not (installer.prefix / "current").exists()
    if kind == "file":
        assert installer.binary.read_text() == "other installation"
    else:
        assert installer.binary.readlink() == Path("/does/not/exist")


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
    assert (installer.root / "custom-config/books.json").is_file()
    calls = [
        json.loads(line)
        for line in (installer.root / "commands.jsonl").read_text().splitlines()
    ]
    pip = next(row for row in calls if row[:2] == ["pip", "install"])
    assert pip[pip.index("--default-index") + 1] == "https://pypi.org/simple"


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
