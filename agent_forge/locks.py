"""Locks and where each tool keeps its state.

A lock is an flock on a file in the tool's state directory. The kernel
drops it however its holder dies, so a lock file left behind is never
mistaken for a live holder; the holder's one line in it says who has
it, for humans and for `holder()`.
"""

import contextlib
import fcntl
import os


def state_dir(project, tool, environ=None, create=True):
    """~/.local/state/agent-forge/<project>/<tool>, created unless
    `create` is false (a tool reading another's state). FORGE_STATE_DIR
    moves the root (tests, and hosts that keep state elsewhere). The home
    directory is `environ`'s HOME, so a caller's environment is honoured
    whole."""
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")
    root = environ.get("FORGE_STATE_DIR") or os.path.join(
        environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"),
        "agent-forge")
    path = os.path.join(root, project, tool)
    if create:
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


# The installation lock (agent-forge's own code, not a project's state).
# forge-cron-run holds it shared for the whole of every job, and hands it
# down to what the job starts (a forge-pr-check worker outlives its
# tick), so a job never runs part on one release and part on another.
# forge-install takes it exclusively to move the installation, after
# leaving the "wanted" marker that stops new jobs starting, so that a
# host whose checks never all stop at once still drains for it.
INSTALL_LOCK_FD = "FORGE_INSTALL_LOCK_FD"
# A marker older than this was left by an installer that died; ignored.
INSTALL_WANTED_MAX_S = 3600


def install_lock_path(install):
    """`<install>.lock`, beside the installation rather than in it, so a
    fresh clone renamed into place keeps the same lock."""
    return os.path.abspath(install).rstrip("/") + ".lock"


def install_wanted_path(install):
    return os.path.abspath(install).rstrip("/") + ".install-wanted"


def install_wanted(install, now=None):
    """Whether an installer is waiting to move `install`: its marker
    exists and is younger than INSTALL_WANTED_MAX_S."""
    import time
    try:
        age = (time.time() if now is None else now) - os.stat(
            install_wanted_path(install)).st_mtime
    except OSError:
        return False
    return age < INSTALL_WANTED_MAX_S


def inherited_install_lock(environ=None):
    """The descriptor of the installation lock this process was handed
    (`$FORGE_INSTALL_LOCK_FD`), to pass on to a process it starts, or
    None when it has none."""
    environ = os.environ if environ is None else environ
    try:
        fd = int(environ.get(INSTALL_LOCK_FD, ""))
        os.fstat(fd)
    except (ValueError, OSError):
        return None
    return fd
