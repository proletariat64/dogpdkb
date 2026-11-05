"""Configuration precedence, isolation, and pipeline propagation without network."""

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from mdocr import config as settings
from scripts import publish_web
from scripts import sync_knowledge_base as pipeline


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "project.toml"
    path.write_text(
        '[skylark]\nmcp_url = "https://example.invalid/mcp"\n'
        "timeout_seconds = 12\nmax_retries = 2\n"
        '[paths]\ncorpus = "corpus"\nweb = "site"\n'
        '[images]\nurl_rewrites = [{from="https://old.invalid/", to="https://new.invalid/"}]\n'
    )
    books = tmp_path / "skylark_books/roots.json"
    books.parent.mkdir()
    books.write_text(
        json.dumps(
            [
                {"key": "one", "name": "First", "url": "https://example.invalid/one"},
                {"key": "two", "name": "Second", "url": "https://example.invalid/two"},
            ]
        )
    )
    return path


@pytest.fixture(autouse=True)
def config_environment(monkeypatch):
    for name in (
        "DOGPDKB_CONFIG",
        "DOGPDKB_BOOKS_FILE",
        "DOGPDKB_CORPUS",
        "DOGPDKB_WEB",
        "DOGPDKB_STATE",
        "DOGPDKB_STAGING",
        "DOGPDKB_GRAPH_DIR",
        "GRAPHIFY_PYTHON",
        "SKYLARK_MCP_URL",
        "UTOO_PROXY_BIN",
        "SKYLARK_TIMEOUT_MS",
        "SKYLARK_MAX_RETRIES",
        "SKYLARK_CONCURRENCY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "_active", None)


def test_default_selection_and_config_path_precedence(
    config_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    default = settings.load_config()
    assert default.file == settings.DEFAULT_CONFIG
    assert settings.select_book(default).key == "fund-platform"
    monkeypatch.setenv("DOGPDKB_CONFIG", str(config_file))
    assert settings.load_config().file == config_file
    assert settings.load_config(settings.DEFAULT_CONFIG).file == settings.DEFAULT_CONFIG


def test_env_values_override_file_and_relative_paths(
    config_file, monkeypatch, tmp_path
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = settings.load_config(config_file)
    assert config.corpus == config_file.parent / "corpus"
    assert config.web == config_file.parent / "site"
    assert config.timeout_seconds == 12
    monkeypatch.setenv("SKYLARK_MCP_URL", "https://override.invalid/mcp")
    monkeypatch.setenv("SKYLARK_TIMEOUT_MS", "2500")
    monkeypatch.setenv("SKYLARK_MAX_RETRIES", "0")
    monkeypatch.setenv("DOGPDKB_CORPUS", "override")
    config = settings.load_config(config_file)
    assert config.mcp_url == "https://override.invalid/mcp"
    assert config.timeout_seconds == 2.5
    assert config.max_retries == 0
    assert config.corpus == elsewhere / "override"
    assert settings.select_book(config, "one").corpus == config.corpus


def test_book_selection_requires_key_and_isolates_corpora(config_file):
    config = settings.load_config(config_file)
    with pytest.raises(ValueError, match="exactly one"):
        settings.select_book(config)
    one = settings.select_book(config, "one")
    two = settings.select_book(config, "two")
    assert one.corpus == config_file.parent / "corpus/one"
    assert two.corpus == config_file.parent / "corpus/two"
    with pytest.raises(ValueError, match="either"):
        settings.select_book(config, "one", "https://example.invalid/three")
    # Legacy CLI URLs work even without a book registry.
    config.books_file.unlink()
    assert (
        settings.select_book(config, url="https://example.invalid/three").key == "adhoc"
    )


@pytest.mark.parametrize(
    "extra",
    [
        "[unknown]\nvalue=1",
        '[skylark]\nmcp_url="file:///tmp/mcp"',
        '[skylark]\nmcp_url="https://example.invalid/?token=secret"',
        '[skylark]\nmcp_url="https://example.invalid/"\nconcurrency=0',
        '[skylark]\nmcp_url="https://example.invalid/"\nmax_retries=1.5',
        '[skylark]\nmcp_url="https://example.invalid/"\ntimeout_seconds=nan',
        '[images]\nurl_rewrites=[{from="",to="https://example.invalid/"}]',
        '[web]\ntopics=[{id="wrong"}]',
    ],
)
def test_invalid_config_fails_early(tmp_path, extra):
    path = tmp_path / "invalid.toml"
    path.write_text(extra)
    with pytest.raises(ValueError):
        settings.load_config(path)


def test_missing_explicit_config_does_not_fall_back(tmp_path):
    with pytest.raises(ValueError, match="cannot load config"):
        settings.load_config(tmp_path / "missing.toml")


def test_pipeline_cli_overrides_file_and_env(config_file, tmp_path, monkeypatch):
    captured = []
    monkeypatch.setenv("DOGPDKB_WEB", str(tmp_path / "env-web"))
    monkeypatch.setattr(pipeline, "run_pipeline", captured.append)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync",
            "--config",
            str(config_file),
            "--book",
            "two",
            "--input",
            str(tmp_path / "cli-corpus"),
            "--web-dir",
            str(tmp_path / "cli-web"),
            "--dry-run",
        ],
    )
    pipeline.main()
    options = captured[0]
    assert options.input_dir == tmp_path / "cli-corpus"
    assert options.web_dir == tmp_path / "cli-web"
    assert options.source_url == "https://example.invalid/two"
    assert options.settings.file == config_file


def test_transport_and_images_share_selected_config(config_file):
    from scripts import get_sync_updates, sync_formats

    settings.configure(argparse.ArgumentParser(), config_file)
    assert get_sync_updates.server_url() == "https://example.invalid/mcp"
    assert get_sync_updates.timeout_s() == 12
    assert (
        sync_formats.rewrite_image_urls("https://old.invalid/a")
        == "https://new.invalid/a"
    )


def test_web_rules_are_configurable(config_file, tmp_path):
    config = replace(
        settings.load_config(config_file),
        groups={"Custom": "Group"},
        group_prefixes={"Pref": "Prefix"},
        topics=(
            {"id": 42, "name": "Topic", "domain": "Domain", "description": "Desc"},
        ),
    )
    (tmp_path / "Custom__Doc-2026-11-01-[1].md").write_text("doc")
    graph = {"nodes": [{"id": "n", "community": 42, "label": "Concept"}], "links": []}
    catalog = publish_web.build_catalog(tmp_path, graph, {"name": "Book"}, config)
    assert catalog["documents"][0]["group"] == "Group"
    assert catalog["topics"][0]["name"] == "Topic"
    assert catalog["topics"][0]["count"] == 1
    assert publish_web.group_for("Prefix category", config) == "Prefix"


def test_config_propagates_and_resume_rejects_changed_settings(config_file, tmp_path):
    from tests.test_pipeline import fake_runner, options

    config = options(tmp_path)
    config.settings = settings.load_config(config_file)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    recorded = []
    delegate = fake_runner(config.staging, [])

    def runner(command):
        assert command[command.index("--config") + 1] == str(config_file)
        recorded.append(Path(command[1]).name)
        delegate(command)

    pipeline.run_pipeline(config, runner)
    assert len(recorded) == 4
    config.resume = True
    config.settings = replace(config.settings, mcp_url="https://changed.invalid/mcp")
    with pytest.raises(ValueError, match="configuration changed"):
        pipeline.run_pipeline(config, lambda command: pytest.fail("must not execute"))


def test_publish_cli_uses_config_from_another_directory(config_file, tmp_path):
    corpus = tmp_path / "corpus/one"
    graph_dir = corpus / "graphify-out"
    graph_dir.mkdir(parents=True)
    (corpus / "Custom__Doc-2026-11-01-[1].md").write_text("doc")
    (graph_dir / "graph.json").write_text(json.dumps({"nodes": [], "links": []}))
    script = Path(__file__).resolve().parents[1] / "scripts/publish_web.py"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            str(config_file),
            "--book",
            "one",
        ],
        cwd=elsewhere,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["documents"] == 1
    output = json.loads((tmp_path / "site/one/catalog.json").read_text())
    assert output["book"] == {"name": "First", "url": "https://example.invalid/one"}


def test_invalid_cli_config_fails_before_writing_or_network(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/sync_knowledge_base.py"
    staging = tmp_path / "must-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            str(tmp_path / "missing.toml"),
            "--staging",
            str(staging),
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "cannot load config" in result.stderr
    assert not staging.exists()
