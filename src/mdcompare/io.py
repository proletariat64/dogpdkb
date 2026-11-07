"""Read-only input access and private atomic report publication."""

import os
import tempfile
from pathlib import Path


class InputError(OSError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class InputReader:
    def __init__(self, max_file_bytes: int):
        self.max_file_bytes = max_file_bytes
        self.paths: set[Path] = set()

    def read_bytes(self, path: Path) -> bytes:
        self.paths.add(path.resolve())
        try:
            with path.open("rb") as f:
                data = f.read(self.max_file_bytes + 1)
        except OSError as exc:
            raise InputError("INPUT_READ_FAILED") from exc
        if len(data) > self.max_file_bytes:
            raise InputError("FILE_BUDGET_EXCEEDED")
        return data


def atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".mdcompare-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
