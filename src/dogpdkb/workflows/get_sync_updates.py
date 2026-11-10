#!/usr/bin/env python3
"""List new, updated, moved, renamed and deleted Yuque docs.

Compares a local directory of flattened Yuque markdown exports
(filenames like `...-YYYY-MM-DD-[doc_id].md`) against the full doc list
of a Yuque book fetched through the local skylark MCP bridge
(utoo-proxy), and writes a sync list.

Usage:
    python scripts/get_sync_updates.py \
        -i graphify_flatten_ready \
        -s https://yuque.antfin.com/global-fund-platform/gom7xx \
        -o tmp [--json]

Output (in -o dir):
    sync-updates-<slug>-<YYYYMMDD>.md     always
    sync-updates-<slug>-<YYYYMMDD>.json   only with --json

No writes to Yuque; read-only listing. The MCP gateway paginates at 50
docs/call and serializes requests (~2 concurrency), so a 7k-doc book
takes a few minutes.

Env overrides (same as the skylark pi extension):
    UTOO_PROXY_BIN      utoo-proxy binary path (default ~/.utoo-proxy/utoo-proxy)
    SKYLARK_MCP_URL     gateway URL (default from default.toml)
    SKYLARK_TIMEOUT_MS  per-call timeout (default 60000)
    SKYLARK_MAX_RETRIES retries for retryable envelopes (default 5)
"""

import argparse
import errno
import json
import os
import random
import re
import selectors
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

from dogpdkb.instance_lock import locked_main, subprocess_lock_options
from dogpdkb.sync_progress import progress_for_config
from dogpdkb.sync_state import StateError
from dogpdkb.sync_state import load_state as canonical_load
from dogpdkb.sync_state import save_state as canonical_save
from dogpdkb.sync_time import UTC8, parse_timestamp, timestamp_record
from mdocr.config import (
    cli_path,
    config_arguments,
    configure,
    configured_book,
    current_config,
)

PROTOCOL_VERSION = "2024-11-05"
PAGE_SIZE = 50
MAX_RETRY_DELAY_S = 30.0
_LISTING_CANCEL: ContextVar[Event | None] = ContextVar("listing_cancel", default=None)


def _check_cancelled() -> None:
    cancelled = _LISTING_CANCEL.get()
    if cancelled is not None and cancelled.is_set():
        raise KeyboardInterrupt


def _retry_wait(delay: float) -> None:
    if _LISTING_CANCEL.get() is None:
        time.sleep(delay)
        return
    while delay > 0:
        _check_cancelled()
        interval = min(delay, 0.1)
        time.sleep(interval)
        delay -= interval
    _check_cancelled()


LOCAL_NAME_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})-\[(\d+)\]\.md$")


def proxy_bin() -> str:
    return current_config().proxy_bin


def server_url() -> str:
    return current_config().mcp_url


def timeout_s() -> float:
    return current_config().timeout_seconds


def max_retries() -> int:
    return current_config().max_retries


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_timestamp(str(value))
    except ValueError:
        return None


def safe_diagnostic(value: object, limit: int = 300) -> str:
    """Bound a diagnostic and redact common credential forms before persistence."""
    text = str(value)
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;\"']+", "[redacted]", text)
    text = re.sub(
        r"(?i)([\"']?(?:[\w-]*(?:token|password|secret|credential|api[_-]?key)|authorization|cookie)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(https?://[^\s?#]+)\?[^\s]+", r"\1?[redacted]", text)
    return " ".join(text.split())[:limit]


class SkylarkError(RuntimeError):
    def __init__(
        self,
        message: str,
        retryable: bool = False,
        *,
        source: str = "remote",
        code: Any = None,
    ) -> None:
        super().__init__(safe_diagnostic(message))
        self.retryable = retryable
        self.source = source
        self.code = code


def _remote_error(payload: Any, source: str) -> SkylarkError:
    """Classify known wire signals without exposing arbitrary response content."""
    fields: list[dict[str, Any]] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            fields.append(value)
            for key in ("details", "data", "error"):
                collect(value.get(key))

    collect(payload)
    codes = [
        str(row[key]).upper()
        for row in fields
        for key in ("error_code", "code")
        if key in row
    ]
    statuses = [
        str(row[key])
        for row in fields
        for key in ("statusCode", "status_code", "status", "http_status")
        if key in row
    ]
    messages = [
        str(row[key]).strip()
        for row in fields
        for key in ("error_msg", "message")
        if key in row
    ]
    permanent_codes = {
        "-32700",
        "-32600",
        "-32601",
        "-32602",
        "400",
        "401",
        "403",
        "422",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "AUTHENTICATION_FAILED",
        "AUTHENTICATION_REQUIRED",
        "UNAUTHENTICATED",
        "ACCESS_DENIED",
        "TOKEN_EXPIRED",
        "INVALID_TOKEN",
        "AUTH_REQUIRED",
        "PERMISSION_DENIED",
        "INVALID_PARAMS",
        "INVALID_ARGUMENT",
        "BAD_REQUEST",
    }
    permanent = bool(
        set(codes) & permanent_codes or set(statuses) & {"400", "401", "403", "422"}
    )
    permanent = permanent or any(
        re.search(
            r"\b(?:authentication (?:failed|required)|unauthorized|forbidden|permission denied|access denied|invalid (?:params|parameters|arguments?))\b",
            message,
            re.IGNORECASE,
        )
        for message in messages
    )
    temporary_codes = {
        "UPSTREAM_RETRYABLE",
        "ETIMEDOUT",
        "ECONNRESET",
        "ECONNREFUSED",
        "EPIPE",
        "ENETUNREACH",
        "EHOSTUNREACH",
        "REQUEST_TIMEOUT",
        "GATEWAY_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "TOO_MANY_REQUESTS",
        "RATE_LIMITED",
        "408",
        "429",
        "502",
        "503",
        "504",
    }
    timeout = next(
        (
            message
            for message in messages
            if re.fullmatch(
                r"Request timeout for [0-9]+ ms",
                message,
                re.IGNORECASE,
            )
        ),
        None,
    )
    retryable = not permanent and bool(
        any(row.get("retryable") is True for row in fields)
        or set(codes) & temporary_codes
        or set(statuses) & {"408", "429", "502", "503", "504"}
        or timeout
        or any(
            re.fullmatch(
                r"(?:HTTP (?:408|429|502|503|504)|Service Unavailable|Connection reset by peer)",
                message,
                re.IGNORECASE,
            )
            for message in messages
        )
    )
    reason = (
        "authentication, permission or parameter error"
        if permanent
        else timeout
        or (
            "temporary upstream failure" if retryable else "unrecognized upstream error"
        )
    )
    # Wire codes are preserved as metadata; diagnostics include only bounded numeric
    # or uppercase identifier codes, never arbitrary response data.
    code = next(
        (row[key] for row in fields for key in ("error_code", "code") if key in row),
        None,
    )
    labels = [
        f"code={item}" for item in codes if re.fullmatch(r"[A-Z_0-9-]{1,50}", item)
    ]
    labels += [f"status={item}" for item in statuses if re.fullmatch(r"[0-9]{3}", item)]
    return SkylarkError(
        f"{source}: {reason}" + (" " + " ".join(labels) if labels else ""),
        retryable=retryable,
        source=source,
        code=code,
    )


def _call_once(tool: str, args: dict[str, Any]) -> Any:
    """One MCP round trip over a freshly spawned utoo-proxy (line-delimited
    JSON-RPC on stdio: initialize -> notifications/initialized -> tools/call)."""
    _check_cancelled()
    try:
        proc = subprocess.Popen(
            [proxy_bin(), server_url(), "-t", "STREAMABLE_HTTP"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **subprocess_lock_options(),
            text=True,
            bufsize=1,
        )
    except OSError as error:
        raise SkylarkError(
            "cannot start utoo-proxy; check the configured executable",
            source="local transport",
            code=error.errno,
        ) from None
    stdin, stdout = proc.stdin, proc.stdout
    sel = None
    deadline = time.monotonic() + timeout_s()
    responses: dict[int, dict[str, Any]] = {}
    pending = bytearray()

    def send(payload: dict[str, Any]) -> None:
        assert stdin is not None  # pipes requested above
        try:
            stdin.write(json.dumps(payload) + "\n")
            stdin.flush()
        except OSError as error:
            raise SkylarkError(
                f"utoo-proxy exited during {tool}",
                retryable=True,
                source="local transport",
                code=error.errno,
            ) from error

    def wait_for(request_id: int) -> None:
        assert stdout is not None  # pipes requested above
        while request_id not in responses:
            _check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SkylarkError(
                    f"local wait for {tool} timed out after {timeout_s():.2f}s",
                    retryable=True,
                    source="local timeout",
                )
            if b"\n" not in pending:
                poll_timeout = (
                    min(remaining, 0.1)
                    if _LISTING_CANCEL.get() is not None
                    else remaining
                )
                assert sel is not None
                if not sel.select(timeout=poll_timeout):
                    continue
                try:
                    chunk = os.read(stdout.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise SkylarkError(
                        f"utoo-proxy exited early during {tool}",
                        retryable=True,
                        source="local transport",
                    )
                pending.extend(chunk)
                continue
            line, _, rest = pending.partition(b"\n")
            pending[:] = rest
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            mid = message.get("id")
            if isinstance(mid, int):
                if "error" in message:
                    raise _remote_error(message["error"], "remote JSON-RPC")
                responses[mid] = message

    try:
        if stdin is None or stdout is None:  # pragma: no cover - pipes requested
            raise SkylarkError(
                "failed to open utoo-proxy pipes", source="local transport"
            )
        sel = selectors.DefaultSelector()
        os.set_blocking(stdout.fileno(), False)
        sel.register(stdout, selectors.EVENT_READ)
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pi-skylark-sync-list", "version": "1.0.0"},
                },
            }
        )
        wait_for(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }
        )
        wait_for(2)
    except OSError as error:
        raise SkylarkError(
            f"local transport failed during {tool}",
            retryable=error.errno
            in {
                errno.ETIMEDOUT,
                errno.ECONNRESET,
                errno.ECONNREFUSED,
                errno.EPIPE,
                errno.ENETUNREACH,
                errno.EHOSTUNREACH,
            },
            source="local transport",
            code=error.errno,
        ) from None
    finally:
        if sel is not None:
            sel.close()
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
        try:
            if stdin is not None:
                stdin.close()
        except OSError:
            pass
        if stdout is not None:
            stdout.close()

    result = responses[2].get("result")
    if not isinstance(result, dict):
        raise SkylarkError(f"{tool} returned invalid MCP result", source="remote MCP")
    content = result.get("content")
    if not isinstance(content, list):
        raise SkylarkError(f"{tool} returned invalid MCP content", source="remote MCP")
    text = (
        content[0].get("text") if content and isinstance(content[0], dict) else ""
    ) or ""
    if not isinstance(text, str):
        raise SkylarkError(f"{tool} returned invalid text", source="remote MCP")
    if result.get("isError") is True:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"message": text}
        raise _remote_error(payload, "remote MCP")
    if not text.lstrip().startswith("{"):
        raise SkylarkError(
            f"{tool} returned non-JSON payload", source="remote envelope"
        )
    try:
        envelope = json.loads(text)
    except ValueError as error:
        raise SkylarkError(f"{tool} returned invalid JSON") from error
    if not isinstance(envelope, dict):
        raise SkylarkError(
            f"{tool} returned invalid envelope", source="remote envelope"
        )
    if envelope.get("ok") is not True:
        raise _remote_error(envelope, "remote envelope")
    return envelope.get("data")


def call_tool(tool: str, args: dict[str, Any]) -> Any:
    read_tools = {
        "skylark_resolve_url",
        "skylark_book_detail",
        "skylark_doc_list",
        "skylark_doc_detail",
        "skylark_book_toc",
    }
    read_only = tool in read_tools or (
        tool == "skylark_table_call"
        and args.get("action")
        in {
            "table.list",
            "table.get",
            "record.list",
        }
    )
    retries = max_retries() if read_only else 0
    stage = {
        "skylark_doc_list": "list",
        "skylark_doc_detail": "export",
        "skylark_book_toc": "toc",
        "skylark_table_call": "export",
    }.get(tool, "plan" if tool in read_tools else "call")
    context = " ".join(
        f"{key}={args[key]}"
        for key in ("book_id", "offset", "doc_id")
        if key in args and re.fullmatch(r"[0-9]+", str(args[key]))
    )
    location = f"stage={stage} {safe_diagnostic(tool, 80)} {context}".rstrip()
    for attempt in range(retries + 1):
        _check_cancelled()
        try:
            result = _call_once(tool, args)
            _check_cancelled()
            return result
        except SkylarkError as error:
            _check_cancelled()
            if attempt >= retries or not error.retryable:
                raise SkylarkError(
                    f"{location} attempt={attempt + 1}/{retries + 1}: {error}",
                    retryable=error.retryable,
                    source=error.source,
                    code=error.code,
                ) from None
            delay = min(2.0**attempt + random.uniform(0, 0.5), MAX_RETRY_DELAY_S)
            print(
                f"  retry {attempt + 1}/{retries} {location} "
                f"wait={delay:.2f}s source={error.source}: {error}",
                file=sys.stderr,
            )
            _retry_wait(delay)
    raise SkylarkError(f"{tool} failed after {retries + 1} attempts")


def resolve_book(url: str) -> dict[str, Any]:
    data = call_tool("skylark_resolve_url", {"url": url})
    if not isinstance(data, dict) or str(data.get("type", "")).lower() != "book":
        raise SystemExit(f"{url} did not resolve to a Yuque book: {data}")
    detail = call_tool("skylark_book_detail", {"book_id": data["id"]})
    if not isinstance(detail, dict):
        raise SystemExit(f"skylark_book_detail returned no data for {data['id']}")
    return {
        "book_id": data["id"],
        "slug": data.get("slug") or "",
        "name": detail.get("name") or data.get("name") or str(data["id"]),
        "items_count": int(detail.get("items_count") or 0),
    }


def fetch_doc_list(
    book_id: int, items_count: int, cache_dir: Path | None = None, *, on_page=None
) -> list[dict[str, Any]]:
    """Fetch a fresh listing; persisted pages are diagnostic checkpoints only.

    The remote API has no snapshot token. Reusing a page from an earlier attempt
    alongside live pages can silently omit a live document even when the total
    and unique-ID checks pass, so every attempt refreshes every page.
    """
    offsets = list(range(0, items_count or PAGE_SIZE, PAGE_SIZE))
    pages: list[list[dict[str, Any]] | None] = [None] * len(offsets)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_dir.chmod(0o700)

    def fetch_page(offset: int) -> list[dict[str, Any]]:
        cache = (
            cache_dir / f"page-{book_id}-{offset:07}.json"
            if cache_dir is not None
            else None
        )
        data = call_tool(
            "skylark_doc_list",
            {"book_id": book_id, "limit": PAGE_SIZE, "offset": offset},
        )
        if not isinstance(data, list):
            raise SkylarkError(f"unexpected doc_list payload at offset {offset}")
        if cache is not None:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{cache.name}.", suffix=".tmp", dir=cache_dir
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(
                        {"book_id": book_id, "offset": offset, "items": data},
                        stream,
                        ensure_ascii=False,
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                temporary.replace(cache)
            finally:
                temporary.unlink(missing_ok=True)
        if on_page is not None:
            on_page(offset, data)
        return data

    cancelled = Event()

    def fetch(index: int) -> None:
        token = _LISTING_CANCEL.set(cancelled)
        try:
            _check_cancelled()
            offset = offsets[index]
            data = fetch_page(offset)
            pages[index] = data
            print(
                f"  page {index + 1}/{len(offsets)} offset={offset} items={len(data)}",
                file=sys.stderr,
            )
        finally:
            _LISTING_CANCEL.reset(token)

    pool = ThreadPoolExecutor(max_workers=current_config().concurrency)
    try:
        list(pool.map(fetch, range(len(offsets))))
    except BaseException:
        cancelled.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    docs: dict[int, dict[str, Any]] = {}

    def collect(items: list[dict[str, Any]]) -> None:
        for item in items:
            doc_id = item["id"]
            if doc_id in docs:
                raise SkylarkError(f"duplicate document ID {doc_id} in doc_list")
            docs[doc_id] = item

    for page in pages:
        collect(page or [])

    last = pages[-1] if pages else None
    offset = len(offsets) * PAGE_SIZE
    while last is not None and len(last) == PAGE_SIZE:
        last = fetch_page(offset)
        print(f"  extra page offset={offset} items={len(last)}", file=sys.stderr)
        previous_count = len(docs)
        collect(last)
        if last and len(docs) == previous_count:
            raise SkylarkError(f"doc_list stopped advancing at offset {offset}")
        offset += PAGE_SIZE
    if items_count and len(docs) < items_count:
        raise SkylarkError(
            f"incomplete doc_list for book {book_id}: "
            f"expected {items_count}, got {len(docs)}"
        )
    return sorted(docs.values(), key=lambda d: d["id"])


def load_sync_state(
    input_dir: Path, book_key: str | None = None
) -> dict[str, dict[str, str]]:
    path = input_dir / ".sync-state.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read sync state {path}: {exc}") from exc
    if payload.get("version") == 2:
        books = payload.get("books") or {}
        if book_key not in books:
            if len(books) != 1:
                raise SystemExit("select one book for canonical sync state")
            book_key = next(iter(books))
        result = dict(books[book_key].get("legacy_documents") or {})
        for doc_id, record in books[book_key]["documents"].items():
            baseline = record.get("baseline") or {}
            corpus = record.get("corpus") or {}
            if corpus.get("status") == "deleted":
                result.pop(doc_id, None)
            elif corpus.get("status") == "published":
                reference = corpus.get("raw") or {}
                result[doc_id] = {
                    "file": Path(reference.get("path", "")).name,
                    "content_updated_at": (corpus.get("version") or {}).get("utc", ""),
                }
            elif baseline:
                result[doc_id] = {
                    "file": baseline["file"],
                    "content_updated_at": baseline.get("content_updated_at") or "",
                }
        return result
    if payload.get("version") != 1 or not isinstance(payload.get("documents"), dict):
        raise SystemExit(f"unsupported sync state format in {path}")
    return payload["documents"]


def save_sync_state(
    input_dir: Path, records: dict[str, dict[str, str]], book_key: str | None = None
) -> None:
    path = input_dir / ".sync-state.json"
    if path.exists() and json.loads(path.read_text()).get("version") == 2:
        root = canonical_load(input_dir, book_key)
        books = root["books"]
        if book_key not in books:
            if len(books) != 1:
                raise SystemExit("select one book for canonical sync state")
            book_key = next(iter(books))
        documents = books[book_key]["documents"]
        for doc_id, row in records.items():
            record = documents.setdefault(
                doc_id, {"baseline": None, "steps": {}, "pending": []}
            )
            record["corpus"] = {
                "status": "published",
                "version": timestamp_record(row["content_updated_at"])
                if row.get("content_updated_at")
                else None,
                "raw": {"path": row["file"], "stage": "historical_cleaned"},
            }
        canonical_save(input_dir, root)
        return
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {"version": 1, "documents": dict(sorted(records.items()))},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def scan_local(
    input_dir: Path, book_key: str | None = None
) -> dict[int, tuple[date, str, datetime | None]]:
    state = load_sync_state(input_dir, book_key)
    local: dict[int, tuple[date, str, datetime | None]] = {}
    for path in input_dir.glob("*.md"):
        match = LOCAL_NAME_RE.search(path.name)
        if not match:
            continue
        doc_id = match.group(2)
        if int(doc_id) in local:
            raise SystemExit(f"duplicate document ID {doc_id} in local corpus")
        record = state.get(doc_id) or {}
        remote = None
        if record.get("file") == path.name:
            remote = _parse_ts(record.get("content_updated_at"))
        local[int(doc_id)] = (
            date.fromisoformat(match.group(1)),
            path.name,
            remote,
        )
    return local


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC8)
    return value.astimezone(timezone.utc)


def build_lists(
    docs: list[dict[str, Any]],
    local: Mapping[int, tuple[date, str] | tuple[date, str, datetime | None]],
) -> tuple[
    list[tuple[datetime | None, dict[str, Any]]],
    list[tuple[datetime, date, dict[str, Any]]],
]:
    new: list[tuple[datetime | None, dict[str, Any]]] = []
    updated: list[tuple[datetime, date, dict[str, Any]]] = []
    for doc in docs:
        remote = _parse_ts(doc.get("content_updated_at") or doc.get("updated_at"))
        if doc["id"] not in local:
            stamp = remote or _parse_ts(doc.get("created_at"))
            new.append((stamp, doc))
            continue
        record = local[doc["id"]]
        local_date = record[0]
        local_remote = record[2] if len(record) == 3 else None
        if not remote:
            continue
        if local_remote:
            if _as_utc(remote) > _as_utc(local_remote):
                updated.append((remote, local_date, doc))
        elif remote.date() >= local_date:
            updated.append((remote, local_date, doc))
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    new.sort(key=lambda x: x[0] or epoch, reverse=True)
    updated.sort(key=lambda x: x[0], reverse=True)
    return new, updated


def safe_segment(text: str) -> str:
    cleaned = re.sub(r'[/\\:*?"<>|]+', "-", str(text or "untitled").strip())
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-") or "untitled"


def corpus_filename(doc: dict[str, Any], trail: list[str], fallback_date: date) -> str:
    remote = _parse_ts(
        doc.get("content_updated_at") or doc.get("updated_at") or doc.get("created_at")
    )
    stamp = (remote.date() if remote else fallback_date).isoformat()
    title = str(doc.get("title") or f"doc-{doc['id']}")
    stem = "__".join([safe_segment(t) for t in trail if t] + [safe_segment(title)])
    suffix = f"-{stamp}-[{doc['id']}].md"
    while len((stem + suffix).encode("utf-8")) > 250:
        stem = stem[:-1]
    return f"{stem}{suffix}"


def toc_paths(book_id: int) -> dict[int, list[str]]:
    """doc_id -> list of ancestor TOC titles (root first, parent last)."""
    nodes = call_tool("skylark_book_toc", {"book_id": book_id})
    if not isinstance(nodes, list):
        raise SkylarkError("skylark_book_toc returned no list")
    paths: dict[int, list[str]] = {}

    def walk(items: list[dict[str, Any]], prefix: list[str]) -> None:
        for item in items:
            trail = prefix + [str(item.get("title") or "").strip()]
            doc_id = item.get("doc_id")
            if item.get("type") == "DOC" and isinstance(doc_id, int):
                paths.setdefault(doc_id, prefix)
            walk(item.get("children") or [], trail)

    walk(nodes, [])
    return paths


def build_structure_lists(
    docs: list[dict[str, Any]],
    local: Mapping[int, tuple[date, str] | tuple[date, str, datetime | None]],
    paths: dict[int, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    remote = {doc["id"]: doc for doc in docs}
    renamed: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    for doc_id, record in local.items():
        doc = remote.get(doc_id)
        if doc is None or doc_id not in paths:
            continue
        local_date, local_file = record[:2]
        target = corpus_filename(doc, paths[doc_id], local_date)
        if target == local_file:
            continue
        item = {
            "id": doc_id,
            "title": doc.get("title"),
            "format": str(doc.get("format") or doc.get("type") or "?"),
            "created_at": doc.get("created_at"),
            "content_updated_at": doc.get("content_updated_at")
            or doc.get("updated_at"),
            "local_date": local_date.isoformat(),
            "local_file": local_file,
            "file": target,
        }
        match = LOCAL_NAME_RE.search(local_file)
        old_stem = local_file[: match.start()] if match else local_file
        old_parent = old_stem.rsplit("__", 1)[0] if "__" in old_stem else ""
        new_parent = "__".join(safe_segment(t) for t in paths[doc_id] if t)
        (renamed if old_parent == new_parent else moved).append(item)
    renamed.sort(key=lambda item: item["id"])
    moved.sort(key=lambda item: item["id"])
    return renamed, moved


def build_deletions(
    docs: list[dict[str, Any]],
    local: Mapping[int, tuple[date, str] | tuple[date, str, datetime | None]],
    input_dir: Path | None = None,
) -> list[dict[str, Any]]:
    remote_ids = {doc["id"] for doc in docs}
    deleted = []
    for doc_id, record in sorted(local.items()):
        if doc_id in remote_ids:
            continue
        local_date, local_file = record[:2]
        item: dict[str, Any] = {
            "id": doc_id,
            "local_file": local_file,
            "local_date": local_date.isoformat(),
        }
        if input_dir is not None:
            path = input_dir / local_file
            if path.is_file():
                stat = path.stat()
                item.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        deleted.append(item)
    return deleted


@locked_main
def main() -> int | None:
    parser = argparse.ArgumentParser(
        description="List Yuque book additions, updates, moves and deletions."
    )
    config_arguments(parser, book=True)
    parser.add_argument("-i", "--input", help="local md sync dir")
    parser.add_argument("-s", "--skylark-url", help="Yuque book url")
    parser.add_argument("-o", "--output", help="output dir")
    parser.add_argument("--json", action="store_true", help="also write a .json copy")
    parser.add_argument(
        "--page-cache",
        type=cli_path,
        help="resume an interrupted listing from this directory",
    )
    parser.add_argument("--snapshot", help="write a knowledge observation snapshot")
    parser.add_argument("--baseline-snapshot", help="complete earlier observation")
    parser.add_argument("--staging", help="private raw export and checkpoint directory")
    parser.add_argument("--listing", help="complete local listing JSON adapter input")
    parser.add_argument(
        "--details-dir", help="local document detail JSON adapter input"
    )
    parser.add_argument("--pending-state", help="explicit prior pending-state input")
    args = parser.parse_args()

    config = configure(parser, args.config)
    source = configured_book(parser, config, args, args.skylark_url)
    args.skylark_url = source.url
    if args.snapshot:
        args.sync_source = source
        args.sync_config = config
        args.sync_corpus = cli_path(args.input) if args.input else source.corpus
        from dogpdkb.knowledge_capture import CaptureError, run_capture

        try:
            status = run_capture(args)
            if status:
                parser.exit(status)
            return status
        except (CaptureError, StateError) as error:
            parser.exit(1, f"dogpdkb discover --snapshot: {error}\n")
    input_dir = cli_path(args.input) if args.input else source.corpus
    output_dir = cli_path(args.output) if args.output else config.state / source.key
    if not input_dir.is_dir():
        raise SystemExit(f"input dir not found: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Resolving {args.skylark_url} ...", file=sys.stderr)
    book = resolve_book(args.skylark_url)
    base_url = args.skylark_url.rstrip("/") + "/"
    print(
        f"Book: {book['name']} (id {book['book_id']}, {book['items_count']} items)",
        file=sys.stderr,
    )

    print("Fetching full doc list ...", file=sys.stderr)
    progress = progress_for_config(config, source, input_dir)
    if progress:
        progress.metadata["book_id"] = book["book_id"]
        active = progress.root.get("active_run")
        if active and not os.environ.get("DOGPDKB_RUN_STAGING"):
            raise SystemExit("unfinished sync plan exists; resume it before discovery")

    def observed_page(offset, rows):
        if progress:
            progress.observe_page(
                offset,
                [
                    {
                        "id": row["id"],
                        "version": timestamp_record(
                            str(row.get("content_updated_at") or row.get("updated_at")),
                            "content_updated_at"
                            if row.get("content_updated_at")
                            else "updated_at",
                        ),
                    }
                    for row in rows
                ],
            )

    docs = fetch_doc_list(
        book["book_id"], book["items_count"], args.page_cache, on_page=observed_page
    )
    if progress:
        progress.phase("listing", "completed", document_count=len(docs))

    print("Scanning local corpus and fetching TOC ...", file=sys.stderr)
    local = scan_local(input_dir, progress.key if progress else source.key)
    paths = toc_paths(book["book_id"]) if local else {}
    new, updated = build_lists(docs, local)
    if progress:
        by_id = {row["id"]: row for row in docs}
        selected = {row["id"] for _, _, row in updated}
        for value, record in progress.book["documents"].items():
            doc_id = int(value)
            if (
                record.get("pending")
                and doc_id in by_id
                and doc_id in local
                and doc_id not in selected
            ):
                remote = _parse_ts(
                    by_id[doc_id].get("content_updated_at")
                    or by_id[doc_id].get("updated_at")
                )
                if remote:
                    updated.append((remote, local[doc_id][0], by_id[doc_id]))
    renamed, moved = build_structure_lists(docs, local, paths)
    deleted = build_deletions(docs, local, input_dir)

    today = datetime.now(UTC8).date()
    slug = book["slug"] or str(book["book_id"])
    stem = f"sync-updates-{slug}-{today:%Y%m%d}"

    def url(doc: dict[str, Any]) -> str:
        return base_url + str(doc["slug"])

    def fmt(doc: dict[str, Any]) -> str:
        return str(doc.get("format") or doc.get("type") or "?")

    lines = [
        f"# Yuque sync list — {book['name']} (book {book['book_id']})",
        f"Generated {today.isoformat()} · source {args.skylark_url}",
        (
            f"Book items {len(docs)} · local docs {len(local)} · "
            f"missing {len(new)} · updated {len(updated)} · renamed {len(renamed)} "
            f"· moved {len(moved)} · deleted {len(deleted)}"
        ),
        "",
        f"## A. Docs missing locally ({len(new)})",
        "Sorted by content_updated_at desc.",
        "",
        "| # | doc_id | title | format | created | content_updated | url |",
        "|---|--------|-------|--------|---------|-----------------|-----|",
    ]
    for i, (_, doc) in enumerate(new, 1):
        lines.append(
            f"| {i} | {doc['id']} | {doc['title']} | {fmt(doc)} "
            f"| {str(doc.get('created_at') or '')[:10]} "
            f"| {str(doc.get('content_updated_at') or '')[:10]} | {url(doc)} |"
        )
    lines += [
        "",
        f"## B. Docs updated since local export ({len(updated)})",
        "local_date = date encoded in the local filename; remote = content_updated_at.",
        "",
        "| # | doc_id | title | local_date | remote_updated | url |",
        "|---|--------|-------|------------|----------------|-----|",
    ]
    for i, (remote, local_date, doc) in enumerate(updated, 1):
        lines.append(
            f"| {i} | {doc['id']} | {doc['title']} | {local_date.isoformat()} "
            f"| {remote.strftime('%Y-%m-%d %H:%M')} | {url(doc)} |"
        )
    remote_by_id = {doc["id"]: doc for doc in docs}
    for section, title, items in (
        ("C", "Docs renamed", renamed),
        ("D", "Docs moved in TOC", moved),
    ):
        lines += [
            "",
            f"## {section}. {title} ({len(items)})",
            "",
            "| doc_id | title | old file | new file |",
            "|--------|-------|----------|----------|",
        ]
        for item in items:
            lines.append(
                f"| {item['id']} | {item['title']} | {item['local_file']} "
                f"| {item['file']} |"
            )
    lines += [
        "",
        f"## E. Docs deleted remotely ({len(deleted)})",
        "Files are backed up when the delta is applied.",
        "",
        "| doc_id | local file |",
        "|--------|------------|",
    ]
    for item in deleted:
        lines.append(f"| {item['id']} | {item['local_file']} |")

    md_path = output_dir / f"{stem}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {md_path}")

    if args.json:
        payload = {
            "book": book,
            "source_url": args.skylark_url,
            "generated": today.isoformat(),
            "snapshot": {
                "complete": bool(book["items_count"])
                and len(docs) == book["items_count"],
                "document_count": len(docs),
                "document_ids": [doc["id"] for doc in docs],
            },
            "local_doc_count": len(local),
            "new": [
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "format": fmt(doc),
                    "created_at": doc.get("created_at"),
                    "content_updated_at": doc.get("content_updated_at"),
                    "url": url(doc),
                }
                for _, doc in new
            ],
            "updated": [
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "format": fmt(doc),
                    "local_date": local_date.isoformat(),
                    "content_updated_at": remote.isoformat(),
                    "url": url(doc),
                }
                for remote, local_date, doc in updated
            ],
            "renamed": [
                {**item, "url": url(remote_by_id[item["id"]])} for item in renamed
            ],
            "moved": [{**item, "url": url(remote_by_id[item["id"]])} for item in moved],
            "deleted": deleted,
        }
        json_path = output_dir / f"{stem}.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"wrote {json_path}")

    print(
        f"missing={len(new)} updated={len(updated)} renamed={len(renamed)} "
        f"moved={len(moved)} deleted={len(deleted)} "
        f"(book {len(docs)} vs local {len(local)})"
    )


if __name__ == "__main__":
    main()
