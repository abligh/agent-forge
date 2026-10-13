"""Where a project's shared agent memory lives on this host.

Claude Code keeps an agent's memories in a directory per checkout,
`~/.claude/projects/<slug>/memory`, one copy per host. A project whose
agents run on several hosts shares that directory through a git
repository (docs/memory-sync.md): forge-memory-sync keeps the hosts in
step, forge-memory-check asserts the index still loads in full, and
forge-memory-trim shortens its lines safely. This module is what the
three agree on: the configuration's `memory` section, each key of it
overridden on one host by a `FORGE_MEMORY_*` variable.

    key        variable              default
    dir        FORGE_MEMORY_DIR      (required)
    remote     FORGE_MEMORY_REMOTE   (required)
    git_dir    FORGE_MEMORY_GIT_DIR  ~/.<project>-memory.git
    seed_dir   FORGE_MEMORY_SEED_DIR ~/.<project>-memory-seed
    ssh_key    FORGE_MEMORY_SSH_KEY  none: git's own SSH setup
               FORGE_MEMORY_HOST     the host's short name
    branch                           main
    labels                           none
    limit                            DEFAULT_LIMIT
    long_line                        LONG_LINE

The directory isn't found by globbing `~/.claude/projects/*/memory`: a
host can hold several, one per checkout an agent was ever started in.
"""

import os
import socket

from agent_forge import locks

INDEX = "MEMORY.md"
BRANCH = "main"

# The harness's limit on MEMORY.md, as its truncation warning reported
# it ("limit: 24.4KB"). A measured observation, not a documented
# constant: if the harness lowers it, the check goes wrong in the quiet
# direction, so `limit` and `--limit` exist for whoever knows better.
DEFAULT_LIMIT = int(24.4 * 1024)

# One index line past this many bytes is reported by itself: it is
# almost always a pointer someone grew a lesson onto.
LONG_LINE = 200

VARIABLES = {"dir": "FORGE_MEMORY_DIR", "remote": "FORGE_MEMORY_REMOTE",
             "git_dir": "FORGE_MEMORY_GIT_DIR",
             "seed_dir": "FORGE_MEMORY_SEED_DIR",
             "ssh_key": "FORGE_MEMORY_SSH_KEY"}


class NotConfigured(Exception):
    """The project has no `memory` section, or lacks a key this needs."""


def _home(path, environ):
    if path == "~" or path.startswith("~/"):
        home = environ.get("HOME") or os.path.expanduser("~")
        return home + path[1:]
    return path


def settings(project, environ=None, hostname=None):
    """Everything forge-memory-* reads, as a dict, paths expanded.
    NotConfigured when neither the configuration nor the environment
    names the directory and the repository."""
    environ = os.environ if environ is None else environ
    section = project.section("memory") if project is not None else {}
    name = project.name if project is not None else "agents"

    def pick(key, default=None):
        return environ.get(VARIABLES[key]) or section.get(key) or default

    cfg = {
        "dir": pick("dir"),
        "remote": pick("remote"),
        "git_dir": pick("git_dir", "~/.%s-memory.git" % name),
        "seed_dir": pick("seed_dir", "~/.%s-memory-seed" % name),
        "ssh_key": pick("ssh_key"),
        "branch": section.get("branch") or BRANCH,
        "labels": list(section.get("labels", [])),
        "limit": section.get("limit") or DEFAULT_LIMIT,
        "long_line": section.get("long_line") or LONG_LINE,
        "host": environ.get("FORGE_MEMORY_HOST")
        or (hostname or socket.gethostname)().split(".")[0],
        "repo": project.repo if project is not None else None,
        "project": name,
    }
    for key in ("dir", "git_dir", "seed_dir", "ssh_key"):
        if cfg[key]:
            cfg[key] = _home(cfg[key], environ)
    missing = [k for k in ("dir", "remote") if not cfg[k]]
    if missing:
        raise NotConfigured(
            "no memory %s: set memory.%s in the configuration, or %s"
            % (" or ".join(missing), " and memory.".join(missing),
               " and ".join(VARIABLES[k] for k in missing)))
    return cfg


def directory(project, environ=None):
    """Just the memory directory, for the tools that need nothing else:
    $FORGE_MEMORY_DIR, else the configuration's `memory.dir`, else None."""
    environ = os.environ if environ is None else environ
    section = project.section("memory") if project is not None else {}
    path = environ.get("FORGE_MEMORY_DIR") or section.get("dir")
    return _home(path, environ) if path else None


def filed_path(project_name, environ=None):
    """Where forge-memory-sync --file keeps the stop it last reported."""
    return os.path.join(locks.state_dir(project_name, "memory-sync", environ),
                        "filed.json")
