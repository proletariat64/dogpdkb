"""Small durable version facts and per-run records; no workflow framework."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dogpdkb.settings import Book, Settings

STATE_SCHEMA = "dogpdkb-state-1"
RUN_SCHEMA = "dogpdkb-run-1"
UTC8 = timezone(timedelta(hours=8))
_LOCK_FDS: list[int] = []


def now_iso() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error


def atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlink: {path}")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def version_key(version: Any) -> str:
    if isinstance(version, dict):
        version = version.get("utc") or version.get("value")
    if not isinstance(version, str) or not version:
        raise ValueError("a normalized source version is required")
    return version


def process_alive(pid: Any) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, IndexError):
        return False
    return True


def lock_held(path: Path, pid: int | None = None) -> bool:
    """Observe an existing kernel lock without creating or rewriting anything."""
    try:
        with Path(path).open("r") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return pid is None or stream.read().strip() == str(pid)
            fcntl.flock(stream, fcntl.LOCK_UN)
    except OSError:
        return False
    return False


def lock_fds() -> tuple[int, ...]:
    """Children keep live tool work locked if the orchestrator is killed."""
    return tuple(_LOCK_FDS)


class Store:
    def __init__(self, settings: Settings, book: Book):
        self.settings, self.book = settings, book
        self.path = settings.state / book.key / "state.json"
        self.runs = settings.staging / book.key
        self.lock_path = book.corpus / ".dogpdkb.lock"
        if self.path.exists():
            self.data = read_json(self.path)
            if (
                not isinstance(self.data, dict)
                or self.data.get("schema") != STATE_SCHEMA
                or self.data.get("book", {}).get("key") != book.key
                or not isinstance(self.data.get("documents"), dict)
            ):
                raise ValueError(
                    "unsupported orchestrator state; initialize a new state"
                )
        else:
            self.data = {
                "schema": STATE_SCHEMA,
                "book": {"key": book.key, "name": book.name, "url": book.url},
                "observation": None,
                "documents": {},
            }

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def document(self, doc_id: str | int) -> dict:
        return self.data["documents"].setdefault(str(doc_id), {"steps": {}})

    def step(self, doc_id: str | int, name: str) -> dict | None:
        return self.data["documents"].get(str(doc_id), {}).get("steps", {}).get(name)

    def suppressed(self, doc_id: str | int, name: str, version: Any) -> bool:
        record = self.step(doc_id, name)
        return bool(
            record
            and version_key(record["version"]) == version_key(version)
            and record["status"] in {"running", "failed", "unknown"}
        )

    def attempt(self, doc_id, name, version, run_id, **details) -> dict:
        return self.complete(doc_id, name, version, "running", run_id, **details)

    def complete(self, doc_id, name, version, status, run_id, **details) -> dict:
        if status not in {"running", "completed", "failed", "unknown", "skipped"}:
            raise ValueError(f"unsupported step result: {status}")
        version_key(version)
        record = {
            **details,
            "version": version,
            "status": status,
            "run_id": run_id,
            "updated_at": now_iso(),
        }
        self.document(doc_id).setdefault("steps", {})[name] = record
        self.save()
        return record

    @contextmanager
    def lock(self):
        paths = sorted(
            {self.lock_path, self.settings.graph_for(self.book) / ".dogpdkb.lock"}
        )
        descriptors = []
        try:
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
                )
                descriptors.append(descriptor)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError(
                        f"another writer is running for target {path.parent}"
                    ) from error
            if self.path.exists():
                self.data = Store(self.settings, self.book).data
            for descriptor in descriptors:
                os.ftruncate(descriptor, 0)
                os.write(descriptor, str(os.getpid()).encode())
            _LOCK_FDS.extend(descriptors)
            try:
                yield self
            finally:
                for descriptor in descriptors:
                    _LOCK_FDS.remove(descriptor)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def initialize(settings: Settings, book: Book) -> dict:
    """Register filename facts without claiming raw or graph version truth."""
    if not book.corpus.is_dir():
        raise ValueError(f"corpus does not exist: {book.corpus}")
    store = Store(settings, book)
    with store.lock():
        found = {}
        ignored = 0
        for path in sorted(book.corpus.glob("*.md")):
            match = re.search(r"(\d{4}-\d{2}-\d{2})-\[(\d+)\]\.md$", path.name)
            if not match:
                ignored += 1
                continue
            date, doc_id = match.groups()
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError as error:
                raise ValueError(
                    f"invalid corpus filename date: {path.name}"
                ) from error
            if doc_id in found:
                raise ValueError(f"duplicate corpus source ID {doc_id}")
            found[doc_id] = (path, date)
        registered = 0
        for doc_id, (path, date) in found.items():
            if doc_id in store.data["documents"]:
                continue
            document = store.document(doc_id)
            document["baseline"] = {
                "path": str(path),
                "filename_date": date,
                "precision": "day",
            }
            document["corpus"] = {
                "path": str(path),
                "version": {"value": date, "precision": "day"},
            }
            document["graph"] = {"status": "unknown"}
            registered += 1
        store.save()
    return {
        "book": book.key,
        "registered": registered,
        "graph_unknown": sum(
            doc.get("graph", {}).get("status") == "unknown"
            for doc in store.data["documents"].values()
        ),
        "ignored": ignored,
        "state": str(store.path),
    }


class Run:
    def __init__(self, store: Store, directory: Path, data: dict):
        self.store, self.directory, self.data = store, directory, data
        self.path = directory / "run.json"
        self.id = data["run_id"]

    @classmethod
    def create(cls, store: Store, command: str):
        run_id = f"run-{datetime.now(UTC8):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        directory = store.runs / run_id
        directory.mkdir(parents=True)
        data = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "command": command,
            "book": store.data["book"],
            "pid": os.getpid(),
            "lock": str(store.lock_path),
            "status": "running",
            "stage": None,
            "counts": {},
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "ended_at": None,
            "exit_code": None,
            "reason": None,
            "artifacts": {},
            "failures": [],
        }
        run = cls(store, directory, data)
        run.save()
        return run

    @classmethod
    def load(cls, store: Store, reference: str | Path):
        path = Path(reference).expanduser()
        if path.name == "run.json":
            path = path.parent
        if not path.is_dir():
            path = store.runs / path
        data = read_json(path / "run.json")
        if (
            not isinstance(data, dict)
            or data.get("schema") != RUN_SCHEMA
            or data.get("book", {}).get("key") != store.book.key
        ):
            raise ValueError("run does not belong to the selected book or schema")
        return cls(store, path.resolve(), data)

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        atomic_json(self.path, self.data)

    def stage(self, name: str, **counts) -> None:
        self.data.update(stage=name, counts=counts)
        self.save()

    def failure(self, **details) -> None:
        self.data["failures"].append(details)
        self.save()

    def finish(self, status: str, exit_code=0, reason=None) -> None:
        if status not in {"paused", "completed", "partial", "failed", "interrupted"}:
            raise ValueError(f"unsupported run result: {status}")
        self.data.update(
            status=status, exit_code=exit_code, reason=reason, ended_at=now_iso()
        )
        self.save()
