"""Knowledge-sync configuration. Credentials remain in the process environment."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10
    import tomli as tomllib


class ConfigError(ValueError):
    """Invalid user configuration."""


_REPOSITORY_CONFIG = Path(__file__).resolve().parents[2] / "default.toml"
DEFAULT_CONFIG = _REPOSITORY_CONFIG


def default_config() -> Path:
    """Keep source-checkout defaults; installed platforms use caller-owned config."""
    if DEFAULT_CONFIG.is_file():
        return DEFAULT_CONFIG
    return Path.cwd() / "dogpdkb.toml"


@dataclass(frozen=True)
class Book:
    key: str
    name: str
    url: str
    corpus: Path


@dataclass(frozen=True)
class SyncConfig:
    file: Path
    books_file: Path
    corpus: Path
    staging: Path
    state: Path
    web: Path
    graph_dir: Path | None
    graphify_python: Path
    mcp_url: str
    proxy_bin: str
    timeout_seconds: float
    max_retries: int
    concurrency: int
    image_url_rewrites: tuple[tuple[str, str], ...]
    groups: dict[str, str]
    group_prefixes: dict[str, str]
    topics: tuple[dict, ...]
    community_limit: int

    def snapshot(self) -> dict:
        """Non-secret effective settings for resume compatibility checks."""
        try:
            return json.loads(json.dumps(asdict(self), default=str))
        except (TypeError, ValueError) as error:
            raise ConfigError("cannot serialize configuration snapshot") from error


def _url(value: str, key: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key}: expected an HTTP(S) URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(f"{key}: expected an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError(f"{key}: credentials, query and fragment are not allowed")
    return value


def load_config(path: Path | str | None = None) -> SyncConfig:
    """Explicit file / DOGPDKB_CONFIG, otherwise checkout or caller-owned config.

    A supplied file replaces default.toml; absent optional keys use portable
    defaults. Relative file paths are anchored to the selected config file.
    Environment path overrides are anchored to the caller's working directory.
    """
    selected = Path(path or os.environ.get("DOGPDKB_CONFIG") or default_config())
    selected = selected.expanduser().resolve()
    try:
        with selected.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot load config {selected}: {error}") from error
    allowed = {
        "source": {"books_file"},
        "paths": {"corpus", "staging", "state", "web", "graph_dir", "graphify_python"},
        "skylark": {
            "mcp_url",
            "proxy_bin",
            "timeout_seconds",
            "max_retries",
            "concurrency",
        },
        "images": {"url_rewrites"},
        "web": {"groups", "group_prefixes", "topics", "community_limit"},
    }
    for section, values in data.items():
        if section not in allowed or not isinstance(values, dict):
            raise ConfigError(f"unknown config section: {section}")
        unknown = values.keys() - allowed[section]
        if unknown:
            raise ConfigError(f"unknown config keys in {section}: {sorted(unknown)}")

    def value(section, key, default=None, env=None) -> Any:
        result = os.environ.get(env) if env else None
        if result is None:
            result = data.get(section, {}).get(key, default)
        return result

    def string(section, key, default="", env=None):
        result = value(section, key, default, env)
        if not isinstance(result, str) or not result.strip():
            raise ConfigError(f"{section}.{key}: expected a nonempty string")
        return result

    def file_path(section, key, default=None, env=None, *, preserve_symlinks=False):
        result = value(section, key, default, env)
        if result is None:
            return None
        if not isinstance(result, str) or not result.strip():
            raise ConfigError(f"{section}.{key}: expected a nonempty path")
        resolved = Path(result).expanduser()
        base = Path.cwd() if env and env in os.environ else selected.parent
        path = base / resolved
        return path.absolute() if preserve_symlinks else path.resolve()

    def required_path(
        section, key, default, env=None, *, preserve_symlinks=False
    ) -> Path:
        result = file_path(
            section, key, default, env, preserve_symlinks=preserve_symlinks
        )
        if result is None:
            raise ConfigError(f"{section}.{key}: expected a path")
        return result

    def number(section, key, default, env=None, integer=False, minimum: float = 0):
        raw = value(section, key, default, env)
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{section}.{key}: expected a number") from None
        if isinstance(raw, bool) or not math.isfinite(parsed) or parsed < minimum:
            raise ConfigError(f"{section}.{key}: must be at least {minimum}")
        if integer and not parsed.is_integer():
            raise ConfigError(f"{section}.{key}: expected an integer")
        return int(parsed) if integer else parsed

    if "SKYLARK_TIMEOUT_MS" in os.environ:
        timeout = (
            number("skylark", "timeout_seconds", 60, "SKYLARK_TIMEOUT_MS", minimum=1)
            / 1000
        )
    else:
        timeout = number("skylark", "timeout_seconds", 60, minimum=0.001)
    rewrites = value("images", "url_rewrites", [])
    if not isinstance(rewrites, list):
        raise ConfigError("images.url_rewrites: expected an array")
    pairs = []
    for row in rewrites:
        if not isinstance(row, dict) or set(row) != {"from", "to"}:
            raise ConfigError("images.url_rewrites: expected from/to pairs")
        pairs.append((_url(row["from"], "images.from"), _url(row["to"], "images.to")))
    groups = value("web", "groups", {})
    prefixes = value("web", "group_prefixes", {})
    for mapping in (groups, prefixes):
        if not isinstance(mapping, dict) or any(
            not key or not isinstance(item, str) for key, item in mapping.items()
        ):
            raise ConfigError("web groups: expected string mappings")
    topics = value("web", "topics", [])
    if not isinstance(topics, list):
        raise ConfigError("web.topics: expected an array")
    for topic in topics:
        if (
            not isinstance(topic, dict)
            or set(topic) != {"id", "name", "domain", "description"}
            or type(topic["id"]) is not int
            or any(
                not isinstance(topic[key], str)
                for key in ("name", "domain", "description")
            )
        ):
            raise ConfigError(
                "web.topics: expected integer id and name/domain/description"
            )
    proxy = string("skylark", "proxy_bin", "utoo-proxy", "UTOO_PROXY_BIN")
    if "/" in proxy or proxy.startswith("~"):
        proxy = str(file_path("skylark", "proxy_bin", "utoo-proxy", "UTOO_PROXY_BIN"))
    return SyncConfig(
        file=selected,
        books_file=required_path(
            "source", "books_file", "skylark_books/roots.json", "DOGPDKB_BOOKS_FILE"
        ),
        corpus=required_path(
            "paths", "corpus", "graphify_flatten_ready", "DOGPDKB_CORPUS"
        ),
        staging=required_path("paths", "staging", "wash", "DOGPDKB_STAGING"),
        state=required_path("paths", "state", "skylark_books/state", "DOGPDKB_STATE"),
        web=required_path("paths", "web", "web-output", "DOGPDKB_WEB"),
        graph_dir=file_path("paths", "graph_dir", env="DOGPDKB_GRAPH_DIR"),
        graphify_python=required_path(
            "paths",
            "graphify_python",
            sys.executable,
            "GRAPHIFY_PYTHON",
            preserve_symlinks=True,
        ),
        mcp_url=_url(
            string("skylark", "mcp_url", env="SKYLARK_MCP_URL"), "skylark.mcp_url"
        ),
        proxy_bin=proxy,
        timeout_seconds=timeout,
        max_retries=int(
            number("skylark", "max_retries", 5, "SKYLARK_MAX_RETRIES", integer=True)
        ),
        concurrency=int(
            number(
                "skylark",
                "concurrency",
                2,
                "SKYLARK_CONCURRENCY",
                integer=True,
                minimum=1,
            )
        ),
        image_url_rewrites=tuple(pairs),
        groups=groups,
        group_prefixes=prefixes,
        topics=tuple(topics),
        community_limit=int(
            number("web", "community_limit", 120, integer=True, minimum=1)
        ),
    )


def select_book(
    config: SyncConfig, key: str | None = None, url: str | None = None
) -> Book:
    """Select one source. Multiple books require an explicit stable key."""
    if url is not None:
        if key:
            raise ConfigError("use either --book or --skylark-url, not both")
        return Book("adhoc", "", _url(url, "source URL"), config.corpus)
    try:
        records = json.loads(config.books_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(
            f"cannot read books file {config.books_file}: {error}"
        ) from error
    if not isinstance(records, list):
        raise ConfigError("books file: expected an array")
    books = []
    keys = set()
    for row in records:
        if not isinstance(row, dict) or set(row) - {
            "key",
            "name",
            "url",
            "enabled",
            "corpus",
        }:
            raise ConfigError("invalid book entry")
        book_key = row.get("key")
        if not isinstance(book_key, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", book_key
        ):
            raise ConfigError(
                "book key: expected letters, digits, underscore or hyphen"
            )
        if book_key in keys:
            raise ConfigError(f"duplicate book key: {book_key}")
        keys.add(book_key)
        if type(row.get("enabled", True)) is not bool:
            raise ConfigError(f"{book_key}: enabled must be a boolean")
        if not row.get("enabled", True):
            continue
        name = row.get("name", "")
        if not isinstance(name, str):
            raise ConfigError(f"{book_key}: name must be a string")
        source = row.get("url")
        if not isinstance(source, str):
            raise ConfigError(f"{book_key}: missing URL")
        corpus = config.corpus / book_key
        if "corpus" in row:
            if not isinstance(row["corpus"], str) or not row["corpus"]:
                raise ConfigError(f"{book_key}: invalid corpus path")
            corpus = config.books_file.parent / Path(row["corpus"]).expanduser()
        if "DOGPDKB_CORPUS" in os.environ:
            corpus = config.corpus
        books.append(Book(book_key, name, _url(source, book_key), corpus.resolve()))
    matches = [book for book in books if key is None or book.key == key]
    if len(matches) != 1:
        raise ConfigError("select exactly one enabled book with --book <key>")
    return matches[0]


_active: SyncConfig | None = None


def current_config() -> SyncConfig:
    """Runtime boundary used by MCP and image exporters (never at import time)."""
    global _active
    if _active is None:
        _active = load_config()
    return _active


def cli_path(value: str) -> Path:
    """CLI paths are relative to the caller and support quoted home paths."""
    return Path(value).expanduser().absolute()


def config_arguments(parser: argparse.ArgumentParser, *, book=False) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help="TOML config (checkout: default.toml; installed: ./dogpdkb.toml)",
    )
    if book:
        parser.add_argument("--book", help="enabled key from the configured books file")


def configure(parser: argparse.ArgumentParser, path: Path | None) -> SyncConfig:
    global _active
    try:
        _active = load_config(path)
    except ValueError as error:
        parser.error(str(error))
    return _active


def configured_book(parser: argparse.ArgumentParser, config, args, url=None) -> Book:
    try:
        return select_book(config, args.book, url)
    except ValueError as error:
        parser.error(str(error))
