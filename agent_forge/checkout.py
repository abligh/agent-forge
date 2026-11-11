"""Which git object store a checkout uses.

forge-cron-run runs each job in a generation of the cron checkout, a
worktree of it at the commit the job started on (agent-forge #297), so
two jobs that share every object can sit at different paths. Tools that
hand each other commits (the Merger's predictions, read by pr-check)
compare the store, not the path."""
import os


def store(path):
    """The real path of the git directory every worktree of `path`'s
    repository shares: `path`/.git for a main checkout, the `commondir`
    a linked worktree's `.git` file leads to, or `path` itself when it
    is already a git directory. When none can be read (the path is
    gone), the real path of `path`, so an old record still compares
    equal to itself."""
    path = os.path.realpath(path)
    dot = os.path.join(path, ".git")
    try:
        if os.path.isdir(dot):
            return dot
        if os.path.isfile(dot):
            with open(dot) as handle:
                line = handle.read().strip()
            if line.startswith("gitdir:"):
                gitdir = os.path.join(path, line[len("gitdir:"):].strip())
                common = os.path.join(gitdir, "commondir")
                if os.path.isfile(common):
                    with open(common) as handle:
                        return os.path.realpath(os.path.join(
                            gitdir, handle.read().strip()))
                return os.path.realpath(gitdir)
        if os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(
                os.path.join(path, "objects")):
            return path
    except OSError:
        pass
    return path
