"""Process locks shared by jobs and HID transports, without Linux-only imports."""

from contextlib import contextmanager
import os
from pathlib import Path
import sys


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if sys.platform == "win32":
            import portalocker
            try:
                portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException:
                raise RuntimeError("Another operation is already using this device or job.") from None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another operation is already using this device or job.") from None
        yield
    finally:
        os.close(fd)
