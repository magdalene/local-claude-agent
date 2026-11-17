from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class LockHeld(Exception):
    def __init__(self, pid: int):
        self.pid = pid
        super().__init__(f'lock held by pid {pid}')


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just isn't ours
    return True


@contextmanager
def acquire(lock_file: Path):
    """One agent run at a time, by design (see the design conversation: token
    budget, and simplicity of a single in-flight worktree/container). A tick
    that finds the lock held just skips — it does not queue."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
        except ValueError:
            pid = -1
        if pid > 0 and _pid_alive(pid):
            raise LockHeld(pid)
        # Stale lock left by a run that crashed or was killed — reclaim it.

    lock_file.write_text(str(os.getpid()))
    try:
        yield
    finally:
        try:
            if int(lock_file.read_text().strip()) == os.getpid():
                lock_file.unlink()
        except (ValueError, FileNotFoundError):
            pass
