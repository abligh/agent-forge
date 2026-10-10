"""Locks and where each tool keeps its state.

A lock is an flock on a file in the tool's state directory. The kernel
drops it however its holder dies, so a lock file left behind is never
mistaken for a live holder; the holder's one line in it says who has
it, for humans and for `holder()`.
"""

import contextlib
import fcntl
import os


def state_dir(project, tool, environ=None):
    """~/.local/state/agent-forge/<project>/<tool>, created. FORGE_STATE_DIR
    moves the root (tests, and hosts that keep state elsewhere)."""
    environ = os.environ if environ is None else environ
    root = environ.get("FORGE_STATE_DIR") or os.path.join(
        environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "agent-forge")
    path = os.path.join(root, project, tool)
    os.makedirs(path, exist_ok=True)
    return path


def try_lock(path, holder=None):
    """An exclusive, non-blocking lock, held while the returned file stays
    open, or None if another process has it. On taking it the file is
    emptied and `holder` written as its one line, so a dead holder's
    line is never read as current."""
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.truncate(0)
    if holder:
        handle.write(holder + "\n")
    handle.flush()
    return handle


def holder(path):
    """The line a live holder of `path` wrote ("" if it wrote none), or
    None if nobody holds it."""
    probe = try_lock_shared(path)
    if probe is not None:
        probe.close()
        return None
    try:
        with open(path) as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def try_lock_shared(path):
    """A shared, non-blocking probe of `path`'s lock, or None if it is
    held exclusively. Never truncates."""
    try:
        handle = open(path, "r")
    except FileNotFoundError:
        return _Unheld()
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class _Unheld:
    def close(self):
        pass


@contextlib.contextmanager
def locked(path):
    """Hold `path`'s exclusive lock for the `with` block, waiting for it.
    For short read-modify-write sections, never for long work."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
