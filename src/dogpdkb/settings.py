"""The orchestrator's own configuration, independent of every tool package."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, overload
from urllib.parse import urlsplit

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


DEFAULT_DOWNLOAD_CONCURRENCY = 3


class ConfigError(ValueError):
    """The selected instance cannot be configured."""


@dataclass(frozen=True)
class YuqueSource:
    key: str
    url: str
    include_document_ids: frozenset[str] | None = None
    exclude_document_ids: frozenset[str] = frozenset()
    enabled: bool = True

    def allows(self, document_id: str) -> bool:
        if self.include_document_ids is not None:
            return document_id in self.include_document_ids
        return document_id not in self.exclude_document_ids


@dataclass(frozen=True)
class DirectorySource:
    key: str
    path: Path
    enabled: bool = True


Source = YuqueSource | DirectorySource


def document_identity(source_key: str, source_document_id: str) -> str:
    # A fixed-width key fits both state filenames and public tool document IDs;
    # rows also keep the original source key and ID for provenance.
    identity = json.dumps((source_key, source_document_id), ensure_ascii=True)
    return "doc-" + hashlib.sha256(identity.encode("ascii")).hexdigest()


def source_binding(source: Source) -> dict[str, Any]:
    if isinstance(source, DirectorySource):
        return {"type": "directory", "path": str(source.path)}
    result = {
        "type": "yuque",
        "url": source.url,
        # Keep the internal inclusion field stable for v0.3.0 state and runs.
        "document_ids": sorted(source.include_document_ids)
        if source.include_document_ids is not None
        else None,
    }
    if source.exclude_document_ids:
        result["exclude_document_ids"] = sorted(source.exclude_document_ids)
    return result


def source_identity(binding: dict) -> dict:
    """Only the upstream URL or directory binds the durable source identity."""
    if binding.get("type") != "yuque":
        return binding
    return {
        key: value
        for key, value in binding.items()
        if key not in {"document_ids", "exclude_document_ids"}
    }


@dataclass(frozen=True)
class Book:
    key: str
    name: str
    corpus: Path
    sources: tuple[Source, ...]

    def source_bindings(self) -> dict[str, dict[str, Any]]:
        return {source.key: source_binding(source) for source in self.sources}

    def select_sources(self, keys: list[str] | None = None) -> tuple[Source, ...]:
        known = {source.key: source for source in self.sources if source.enabled}
        if keys is None:
            return tuple(known.values())
        if len(keys) != len(set(keys)):
            raise ConfigError("duplicate --source selection")
        for key in keys:
            if key not in known:
                raise ConfigError(f"unknown or disabled source: {key}")
        return tuple(known[key] for key in keys)


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
    mdcompare_config: Path | None = None
    body_recovery: Path | None = None
    semantic_score_request_budget: int | None = None
    semantic_score_model: str | None = None
    ocr_mode: str = "llm"
    ocr_request_budget: int | None = None
    graphify_model: str | None = None
    graphify_token_budget: int | None = None
    ocr_concurrency: int = 3
    ocr_fix: int = 0
    graphify_concurrency: int = 3

    def ocr_policy(self) -> dict:
        llm = self.ocr_mode == "llm"
        return {
            "mode": self.ocr_mode,
            "model": "openai/gpt-6.1-terra" if llm else None,
            "effort": "medium" if llm else None,
            "concurrency": self.ocr_concurrency,
            "fix": self.ocr_fix,
            "request_budget": self.ocr_request_budget,
            "max_seconds": self.ocr_budget_seconds,
            "cache_dir": str(self.ocr_cache),
            "command": self.command("mdocr"),
        }

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

    @overload
    def configured_path(name: str, default: str, env: str | None = None) -> Path: ...

    @overload
    def configured_path(
        name: str, default: None = None, env: str | None = None
    ) -> Path | None: ...

    def configured_path(
        name: str, default: str | None = None, env: str | None = None
    ) -> Path | None:
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
    score = data.get("semantic_score", {})
    if not isinstance(score, dict):
        raise ConfigError("semantic_score: expected a TOML table")
    score_requests, model = score.get("request_budget"), score.get("model")
    if score_requests is not None and (
        type(score_requests) is not int or score_requests < 0
    ):
        raise ConfigError(
            "semantic_score.request_budget: expected a nonnegative integer"
        )
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ConfigError("semantic_score.model: expected provider/model")
    ocr = data.get("ocr", {})
    ocr_mode, ocr_requests = ocr.get("mode", "llm"), ocr.get("request_budget")
    if ocr_mode not in ("llm", "paddle"):
        raise ConfigError("ocr.mode: expected llm or paddle")
    if ocr_requests is not None and (type(ocr_requests) is not int or ocr_requests < 0):
        raise ConfigError("ocr.request_budget: expected a nonnegative integer")
    ocr_fix = ocr.get("fix", 0)
    if type(ocr_fix) is not int or ocr_fix < 0:
        raise ConfigError("ocr.fix: expected a nonnegative integer")
    graphify = data.get("graphify", {})
    if not isinstance(graphify, dict):
        raise ConfigError("graphify: expected a TOML table")
    graphify_model = graphify.get("model")
    if graphify_model is not None and (
        not isinstance(graphify_model, str) or not graphify_model.strip()
    ):
        raise ConfigError("graphify.model: expected a nonempty model ID")
    graphify_budget = graphify.get("token_budget")
    if graphify_budget is not None and (
        type(graphify_budget) is not int or graphify_budget <= 0
    ):
        raise ConfigError("graphify.token_budget: expected a positive integer")
    graphify_concurrency = graphify.get("concurrency", 3)
    if type(graphify_concurrency) is not int or graphify_concurrency < 1:
        raise ConfigError("graphify.concurrency: expected a positive integer")
    ocr_concurrency = ocr.get("concurrency", 3)
    if type(ocr_concurrency) is not int or not 1 <= ocr_concurrency <= 100:
        raise ConfigError("ocr.concurrency: expected an integer from 1 to 100")
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
        ocr_mode=ocr_mode,
        ocr_request_budget=ocr_requests,
        graphify_model=graphify_model,
        graphify_token_budget=graphify_budget,
        graphify_concurrency=graphify_concurrency,
        ocr_concurrency=ocr_concurrency,
        ocr_fix=ocr_fix,
        body_recovery=configured_path("body_recovery"),
        mdcompare_config=configured_path("mdcompare_config"),
        semantic_score_request_budget=score_requests,
        semantic_score_model=model,
        tools=tools,
        web_options=data.get("web", {}),
    )


def _source_key(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]*", value
    ):
        raise ConfigError(
            f"{label} key must contain letters, digits, underscore or hyphen"
        )
    return value


_SCOPE_KEYS = ("document_ids", "include_document_ids", "exclude_document_ids")


def _subscription_ids(value, source_key, field):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ConfigError(f"{source_key}: {field} must be nonempty unique string IDs")
    return frozenset(value)


def _parse_sources(row: dict, base: Path, book_key: str) -> tuple[Source, ...]:
    values = row.get("sources")
    if (
        "url" in row
        or any(field in row for field in _SCOPE_KEYS)
        or not isinstance(values, list)
        or not values
    ):
        raise ConfigError(
            f"{book_key}: configure a nonempty sources list instead of top-level url/document_ids"
        )
    sources: list[Source] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ConfigError(f"{book_key}: source must be an object")
        key = _source_key(value.get("key"), "source")
        if key in seen:
            raise ConfigError(f"{book_key}: duplicate source key: {key}")
        seen.add(key)
        enabled = value.get("enabled", True)
        if type(enabled) is not bool:
            raise ConfigError(f"{key}: source enabled must be boolean")
        kind = value.get("type")
        if kind == "yuque":
            if "path" in value:
                raise ConfigError(f"{key}: yuque source does not accept path")
            fields = [field for field in _SCOPE_KEYS if field in value]
            if len(fields) > 1:
                raise ConfigError(
                    f"{key}: document_ids, include_document_ids and exclude_document_ids are mutually exclusive"
                )
            include, exclude = None, frozenset()
            if fields:
                field = fields[0]
                ids = _subscription_ids(value[field], key, field)
                if field == "exclude_document_ids":
                    exclude = ids
                else:
                    include = ids
            sources.append(
                YuqueSource(key, _url(value.get("url"), key), include, exclude, enabled)
            )
        elif kind == "directory":
            if "url" in value or any(field in value for field in _SCOPE_KEYS):
                raise ConfigError(
                    f"{key}: directory source does not accept url or document ID policies"
                )
            path = value.get("path")
            if not isinstance(path, str) or not path.strip():
                raise ConfigError(f"{key}: directory source requires a path")
            sources.append(
                DirectorySource(
                    key, (base / Path(path).expanduser()).resolve(), enabled
                )
            )
        else:
            raise ConfigError(f"{key}: source type must be yuque or directory")
    return tuple(sources)


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
        sources = _parse_sources(row, settings.books_file.parent, name)
        if not row.get("enabled", True):
            continue
        if not any(source.enabled for source in sources):
            raise ConfigError(f"{name}: subscription must have an enabled source")
        corpus = settings.corpus / name
        if "corpus" in row:
            if not isinstance(row["corpus"], str) or not row["corpus"]:
                raise ConfigError("book corpus must be a path")
            corpus = settings.books_file.parent / Path(row["corpus"]).expanduser()
        if "DOGPDKB_CORPUS" in os.environ:
            corpus = settings.corpus
        books.append(Book(name, str(row.get("name", "")), corpus.resolve(), sources))
    matches = [book for book in books if key is None or book.key == key]
    if len(matches) != 1:
        raise ConfigError("select exactly one enabled book with --book")
    return matches[0]


def select_inputs(
    settings: Settings, book: Book, keys: list[str] | None, directory: Path | None
) -> tuple[Source, ...]:
    if directory is None:
        return book.select_sources(keys)
    try:
        root = directory.expanduser().resolve()
    except RuntimeError as error:
        raise ConfigError(f"invalid directory source {directory}: {error}") from error
    validate_directory_source(settings, book, root)
    matches = tuple(
        source
        for source in book.sources
        if isinstance(source, DirectorySource) and source.path == root
    )
    if len(matches) > 1:
        raise ConfigError(
            f"directory matches multiple sources: {root}; choose --source KEY"
        )
    if matches:
        return matches
    key = "direct-" + document_identity(book.key, str(root)).removeprefix("doc-")
    if key in book.source_bindings():
        raise ConfigError(
            f"generated directory source key conflicts with configured source: {key}"
        )
    return (DirectorySource(key, root),)


def validate_directory_source(settings: Settings, book: Book, root: Path) -> None:
    if not root.is_dir():
        raise ConfigError(
            f"directory source does not exist or is not a directory: {root}"
        )
    for target in (
        book.corpus,
        settings.graph_for(book),
        settings.staging,
        settings.state,
        settings.ocr_cache,
        settings.web,
        settings.results,
    ):
        if root.is_relative_to(target) or target.is_relative_to(root):
            raise ConfigError(f"directory source overlaps a managed path: {target}")
