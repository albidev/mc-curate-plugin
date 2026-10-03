"""A held OS file lock identifies the detached worker, independently of PID reuse."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path


def _try_lock(handle) -> bool:
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


@contextmanager
def hold(job_path: Path, inherited_fd=None):
    lock_path = job_path.with_suffix('.lock')
    handle = None
    if type(inherited_fd) is int and inherited_fd >= 0:
        try:
            inherited_stat = os.fstat(inherited_fd)
            expected_stat = lock_path.stat()
        except OSError:
            pass  # A newly spawned process need not have the old launcher's descriptor.
        else:
            if (inherited_stat.st_dev, inherited_stat.st_ino) == (expected_stat.st_dev, expected_stat.st_ino):
                handle = os.fdopen(inherited_fd, 'r+b')
    inherited = handle is not None
    if handle is None:
        handle = lock_path.open('a+b')
    with handle:
        if not inherited:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'\0'); handle.flush()
            if not _try_lock(handle):
                raise RuntimeError('Another worker owns this reconciliation job.')
        # hermes_bootstrap can exec into a newer managed interpreter; the lease must survive.
        os.set_inheritable(handle.fileno(), True)
        yield handle


def held(job_path: Path) -> bool:
    try:
        handle = job_path.with_suffix('.lock').open('r+b')
    except FileNotFoundError:
        return False
    with handle:
        return not _try_lock(handle)
