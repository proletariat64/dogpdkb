"""The orchestrator's own configuration, independent of every tool package."""

from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class ConfigError(ValueError):
    """The selected instance cannot be configured."""


@dataclass(frozen=True)
class Book:
    key: str
    name: str
    url: str
    corpus: Path
    document_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Settings:
    file: Path
    books_file: Path
    corpus: Path
    staging: Path
    state: Path
    web: Path
    results: Path
    ocr_cache: Path
    graph_dir: Path | None
    mcp_url: str
    proxy_bin: str
    timeout_seconds: float
    ocr_budget_seconds: float
    tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    web_options: dict[str, Any] = field(default_factory=dict)

    def graph_for(self, book: Book) -> Path:
        return self.graph_dir or book.corpus / "graphify-out"

    def command(self, name: str) -> list[str]:
        return list(self.tools.get(name, (name,)))

    def skylark_environment(self) -> dict[str, str]:
        values = {
            "UTOO_PROXY_BIN": self.proxy_bin,
            "SKYLARK_TIMEOUT_MS": str(int(self.timeout_seconds * 1000)),
        }
        if self.mcp_url:
            values["SKYLARK_MCP_URL"] = self.mcp_url
        return values

    @property
    def groups(self):
        return self.web_options.get("groups", {})

    @property
    def group_prefixes(self):
        return self.web_options.get("group_prefixes", {})

    @property
    def topics(self):
        return self.web_options.get("topics", [])

    @property
    def community_limit(self):
        return self.web_options.get("community_limit", 120)


def default_config() -> Path:
    checkout = Path(__file__).resolve().parents[2] / "default.toml"
    return checkout if checkout.is_file() else Path.cwd() / "dogpdkb.toml"


def _url(value: Any, label: str, *, optional=False) -> str:
    if optional and not value:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{label}: expected HTTP(S) URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError(f"{label}: expected HTTP(S) URL without credentials")
    return value


def load_config(path: Path | str | None = None) -> Settings:
    selected = Path(path or os.environ.get("DOGPDKB_CONFIG") or default_config())
    selected = selected.expanduser().resolve()
    try:
        with selected.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot load config {selected}: {error}") from error
    if any(not isinstance(v, dict) for v in data.values()):
        raise ConfigError("configuration sections must be tables")

    def configured_path(name: str, default=None, env=None):
        value = os.environ.get(env) if env else None
        base = Path.cwd() if value is not None else selected.parent
        value = value if value is not None else data.get("paths", {}).get(name, default)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ConfigError(f"paths.{name}: expected path")
        return (base / Path(value).expanduser()).resolve()

    def number(section: str, name: str, default: float):
        value = data.get(section, {}).get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{section}.{name}: expected number")
        if not math.isfinite(value) or value <= 0:
            raise ConfigError(f"{section}.{name}: must be positive and finite")
        return float(value)

    tools = {}
    for name, value in data.get("tools", {}).items():
        argv = [value] if isinstance(value, str) else value
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            raise ConfigError(f"tools.{name}: expected executable or argv array")
        argv = list(argv)
        if "/" in argv[0] or argv[0].startswith("~"):
            argv[0] = str((selected.parent / Path(argv[0]).expanduser()).absolute())
        tools[name] = tuple(argv)
    books = data.get("source", {}).get("books_file", "books.json")
    if not isinstance(books, str) or not books:
        raise ConfigError("source.books_file: expected path")
    skylark = data.get("skylark", {})
    proxy = os.environ.get("UTOO_PROXY_BIN", skylark.get("proxy_bin", "utoo-proxy"))
    if not isinstance(proxy, str) or not proxy:
        raise ConfigError("skylark.proxy_bin: expected executable")
    if "/" in proxy or proxy.startswith("~"):
        proxy = str((selected.parent / Path(proxy).expanduser()).absolute())
    return Settings(
        file=selected,
        books_file=(selected.parent / Path(books).expanduser()).resolve(),
        corpus=configured_path("corpus", "corpus", "DOGPDKB_CORPUS"),
        staging=configured_path("staging", "runs", "DOGPDKB_STAGING"),
        state=configured_path("state", "state", "DOGPDKB_STATE"),
        web=configured_path("web", "web", "DOGPDKB_WEB"),
        results=configured_path("results", "results"),
        ocr_cache=configured_path("ocr_cache", "ocr-cache"),
        graph_dir=configured_path("graph_dir", env="DOGPDKB_GRAPH_DIR"),
        mcp_url=_url(
            os.environ.get("SKYLARK_MCP_URL", skylark.get("mcp_url", "")),
            "skylark.mcp_url",
            optional=True,
        ),
        proxy_bin=proxy,
        timeout_seconds=number("skylark", "timeout_seconds", 60),
        ocr_budget_seconds=number("ocr", "budget_seconds", 1800),
        tools=tools,
        web_options=data.get("web", {}),
    )


def select_book(settings: Settings, key: str | None = None) -> Book:
    try:
        rows = json.loads(settings.books_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read books file: {error}") from error
    if not isinstance(rows, list):
        raise ConfigError("books file must be an array")
    books, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigError("book must be an object")
        name = row.get("key")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", name
        ):
            raise ConfigError(
                "book key must contain letters, digits, underscore or hyphen"
            )
        if name in seen:
            raise ConfigError(f"duplicate book key: {name}")
        seen.add(name)
        if type(row.get("enabled", True)) is not bool:
            raise ConfigError("book enabled must be boolean")
        if not row.get("enabled", True):
            continue
        corpus = settings.corpus / name
        if "corpus" in row:
            if not isinstance(row["corpus"], str) or not row["corpus"]:
                raise ConfigError("book corpus must be a path")
            corpus = settings.books_file.parent / Path(row["corpus"]).expanduser()
        if "DOGPDKB_CORPUS" in os.environ:
            corpus = settings.corpus
        document_ids = row.get("document_ids")
        if document_ids is not None:
            if (
                not isinstance(document_ids, list)
                or not document_ids
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in document_ids
                )
                or len(set(document_ids)) != len(document_ids)
            ):
                raise ConfigError(
                    "book document_ids: expected nonempty unique string IDs"
                )
            document_ids = tuple(sorted(document_ids))
        books.append(
            Book(
                name,
                str(row.get("name", "")),
                _url(row.get("url"), name),
                corpus.resolve(),
                document_ids,
            )
        )
    matches = [book for book in books if key is None or book.key == key]
    if len(matches) != 1:
        raise ConfigError("select exactly one enabled book with --book")
    return matches[0]
