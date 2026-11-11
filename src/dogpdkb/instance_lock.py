"""One user-wide updater lock, inherited by cooperating workflow subprocesses."""

from __future__ import annotations

import fcntl
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path

LOCK_FD_ENV = "DOGPDKB_UPDATE_LOCK_FD"
_current_fd: int | None = None


class UpdateAlreadyRunning(RuntimeError):
    """Another updater owns the user's instance lock."""


def _lock_path() -> Path:
    directory = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return directory / "dogpdkb/sync.lock"


def _inherited_fd(path: Path) -> int | None:
    """An environment value alone never grants entry: verify its open file."""
    try:
        fd = int(os.environ[LOCK_FD_ENV])
        inherited = os.fstat(fd)
        target = path.stat()
    except (KeyError, ValueError, OSError):
        return None
    if (
        not stat.S_ISREG(inherited.st_mode)
        or inherited.st_uid != os.getuid()
        or (inherited.st_dev, inherited.st_ino) != (target.st_dev, target.st_ino)
    ):
        return None
    return fd


@contextmanager
def updater_lock() -> Iterator[None]:
    """Hold the lock until every inheriting updater closes its descriptor."""
    global _current_fd
    if _current_fd is not None:
        yield
        return
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = _inherited_fd(path)
    if fd is None:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(fd)
        raise UpdateAlreadyRunning(
            "UPDATE_ALREADY_RUNNING: another dogpdkb updater is running"
        ) from error
    except BaseException:
        os.close(fd)
        raise
    previous = os.environ.get(LOCK_FD_ENV)
    _current_fd = fd
    os.environ[LOCK_FD_ENV] = str(fd)
    try:
        yield
    finally:
        _current_fd = None
        if previous is None:
            os.environ.pop(LOCK_FD_ENV, None)
        else:
            os.environ[LOCK_FD_ENV] = previous
        # Do not LOCK_UN: inherited children share this open-file description.
        os.close(fd)


def subprocess_lock_options() -> dict:
    """Explicitly pass the held descriptor through Python's close_fds default."""
    return {"pass_fds": (_current_fd,)} if _current_fd is not None else {}


def lock_is_held() -> bool:
    """Read-only liveness probe for status; never grants or keeps ownership."""
    try:
        fd = os.open(_lock_path(), os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    fcntl.flock(fd, fcntl.LOCK_UN)
    return False


def locked_main(function):
    """Guard a mutating entry point, leaving help/version available."""

    @wraps(function)
    def invoke(*args, **kwargs):
        arguments = list(args[0] if args and args[0] is not None else sys.argv[1:])
        if "--help" in arguments or "-h" in arguments or arguments == ["--version"]:
            return function(*args, **kwargs)
        with ExitStack() as stack:
            try:
                stack.enter_context(updater_lock())
            except (UpdateAlreadyRunning, OSError) as error:
                print(f"dogpdkb: {error}", file=sys.stderr)
                raise SystemExit(1) from error
            return function(*args, **kwargs)

    return invoke
