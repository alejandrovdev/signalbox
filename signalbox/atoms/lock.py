"""The lane's lock: one run at a time per repository, through `flock` on a file in its git
common directory, which every worktree shares.

    probe(path)          "free", "busy" or "unknown", without waiting and without creating it
    take(path, wait)     the open file holding the lock, or None when busy and not waiting

The lock goes with the process: it is released when the file closes, however the run ends.
A child process that should not hold it must not inherit the file (Python's files are not
inherited by default).
"""

import fcntl
import os

FREE, BUSY, UNKNOWN = "free", "busy", "unknown"


def probe(path):
    """Whether the lock is held, without waiting. A lock file nobody made was never taken."""
    try:
        handle = open(path, "r")
    except FileNotFoundError:
        return FREE
    except OSError:
        return UNKNOWN
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return BUSY
        except OSError:
            return UNKNOWN
        fcntl.flock(handle, fcntl.LOCK_UN)
        return FREE


def take(path, wait=True):
    """Take the lock: the open file that holds it, or None when another process holds it and
    `wait` is false. OSError when the file cannot be opened."""
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        handle.close()
        return None
    except BaseException:
        handle.close()
        raise
    os.set_inheritable(handle.fileno(), False)
    return handle
