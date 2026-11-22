"""Process locks shared by jobs and HID transports, without Linux-only imports."""

from contextlib import contextmanager
import os
from pathlib import Path
import sys


@contextmanager
def _lock(path: Path, *, shared=False, blocking=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if sys.platform == "win32":
            import portalocker
            try:
                mode = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
                portalocker.lock(fd, mode if blocking else mode | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException:
                raise RuntimeError("Another operation is already using this device or job.") from None
        else:
            import fcntl
            try:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(fd, mode if blocking else mode | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another operation is already using this device or job.") from None
        yield
    finally:
        os.close(fd)


def exclusive_lock(path: Path):
    return _lock(path)


def shared_lock(path: Path):
    return _lock(path, shared=True, blocking=True)
