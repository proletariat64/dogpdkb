"""Read-only input access and private atomic report publication."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .model import Document


class InputError(OSError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class InputReader:
    """Bounded reads with one byte view per document and observed-drift checks.

    Metadata checks detect ordinary changes; callers must supply immutable
    snapshots. They do not protect against arbitrary concurrent mutation.
    """

    def __init__(self, max_file_bytes: int):
        self.max_file_bytes = max_file_bytes
        self.paths: set[Path] = set()
        self._fingerprints: dict[Path, tuple[int, ...]] = {}
        self._cache: dict[Path, bytes] = {}
        self._dependencies: dict[tuple[str, str], set[Path]] = {}
        self._current: tuple[str, str] | None = None

    @staticmethod
    def _fingerprint(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def begin_document(self, identity: tuple[str, str]) -> None:
        self._current = identity
        self._cache.clear()
        self._dependencies[identity] = set()

    def read_bytes(self, path: Path, *, max_bytes: int | None = None) -> bytes:
        limit = self.max_file_bytes if max_bytes is None else max_bytes
        try:
            path = path.absolute()
            self.paths.add(path)
            if self._current is not None:
                self._dependencies[self._current].add(path)
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise InputError("INPUT_NOT_REGULAR_FILE")
            fingerprint = self._fingerprint(before)
            previous = self._fingerprints.get(path)
            if previous is not None and previous != fingerprint:
                raise InputError("INPUT_CHANGED")
            if path in self._cache:
                return self._cache[path]
            if before.st_size > limit:
                raise InputError("FILE_BUDGET_EXCEEDED")
            with path.open("rb") as stream:
                opened = self._fingerprint(os.fstat(stream.fileno()))
                data = stream.read(limit + 1)
                after = self._fingerprint(os.fstat(stream.fileno()))
            self._fingerprints[path] = fingerprint
            if (
                fingerprint != opened
                or opened != after
                or self._fingerprint(path.stat()) != after
            ):
                raise InputError("INPUT_CHANGED")
        except InputError:
            raise
        except (OSError, ValueError) as exc:
            raise InputError("INPUT_READ_FAILED") from exc
        if len(data) > limit:
            raise InputError("FILE_BUDGET_EXCEEDED")
        self._cache[path] = data
        return data

    def changed_paths(self) -> set[Path]:
        changed = set()
        for path, fingerprint in self._fingerprints.items():
            try:
                if self._fingerprint(path.stat()) != fingerprint:
                    changed.add(path)
            except OSError:
                changed.add(path)
        return changed

    def affected_documents(self, changed: set[Path]) -> set[tuple[str, str]]:
        return {
            identity
            for identity, paths in self._dependencies.items()
            if paths & changed
        }


def referenced_paths(document: Document) -> set[Path]:
    """Include declared evidence even if validation never gets far enough to read it."""
    paths = {document.path}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and (
                    key in {"path", "file", "artifact", "prepared_image"}
                    or key.endswith(("_path", "_file"))
                ):
                    paths.add(document.resolve(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for value in (document.resources, document.ocr, document.provenance):
        visit(value)
    return paths


def _same_file(first: Path, second: Path) -> bool:
    try:
        if first.resolve() == second.resolve():
            return True
        return first.samefile(second)
    except (ValueError, RuntimeError) as exc:
        raise InputError("INPUT_PATH_INVALID") from exc
    except OSError:
        return False


def validate_outputs(outputs: list[Path], inputs: set[Path]) -> None:
    for index, output in enumerate(outputs):
        if any(_same_file(output, source) for source in inputs) or any(
            _same_file(output, other) for other in outputs[:index]
        ):
            raise InputError("REPORT_INPUT_COLLISION")


def _stage(path: Path, content: str) -> Path:
    fd, temporary = tempfile.mkstemp(prefix=".mdcompare-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def publish_reports(reports: list[tuple[Path, str]]) -> None:
    """Stage all reports before publishing; roll back ordinary publication errors.

    Each replacement is atomic. Multiple paths are not a crash-proof filesystem
    transaction; on a caught failure their prior versions are restored.
    """
    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path | None] = {}
    published: list[Path] = []
    try:
        for destination, content in reports:
            staged.append((destination, _stage(destination, content)))
        for destination, _ in staged:
            if destination.exists() or destination.is_symlink():
                fd, backup_name = tempfile.mkstemp(
                    prefix=".mdcompare-backup-", dir=destination.parent
                )
                os.close(fd)
                backup = Path(backup_name)
                backup.unlink()
                os.link(destination, backup, follow_symlinks=False)
                backups[destination] = backup
            else:
                backups[destination] = None
        for destination, temporary in staged:
            os.replace(temporary, destination)
            published.append(destination)
    except BaseException:
        for destination in reversed(published):
            backup = backups[destination]
            if backup is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(backup, destination)
                backups[destination] = None
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)


def atomic_write(path: Path, content: str) -> None:
    publish_reports([(path, content)])
