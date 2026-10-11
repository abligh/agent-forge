"""What each build-check step depends on, found by tracing it, and which
steps a change needs (docs/designs/0005).

A full run on the default branch runs each step under a tracer and
keeps, for each, the paths inside the worktree it opened, executed or
looked for: a *record*, `depends/<tree>.json` in the build check's
state directory. A pull request then runs the steps that read a path it
changes, and every step whenever the record can't answer.

Two tracers:

* `preload` (the default): lib/forge-trace.c, built here with the
  host's C compiler into an `LD_PRELOAD` library. No root and no
  package, and cheap; it misses statically linked programs.
* `strace`, where it is installed: everything, at a higher cost.

The lines a tracer writes are ("x" or "o", absolute path): "x" for a
program executed, "o" for anything else.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile

from agent_forge.proc import sh

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SOURCE = os.path.join(ROOT, "lib", "forge-trace.c")
# The variable the library reads its record's path from.
TRACE_VAR = "FORGE_TRACE_FILE"
TRACERS = ("preload", "strace")
DEFAULT_TRACER = "preload"
# How long records are kept; the newest is kept whatever its age.
KEEP = datetime.timedelta(days=14)
RECORDS = "depends"


# How the library is compiled; part of its name, so a change rebuilds it.
CFLAGS = ("-shared", "-fPIC", "-O2")


def build(dest_dir, run=sh, source=SOURCE):
    """The tracer library, built into `dest_dir` the first time for
    this source and CFLAGS (its name carries their digest), or None if
    the compiler is missing or fails."""
    try:
        with open(source, "rb") as handle:
            digest = hashlib.sha256(handle.read() + " ".join(
                CFLAGS).encode()).hexdigest()[:16]
    except OSError:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    lib = os.path.join(dest_dir, "forge-trace-%s.so" % digest)
    if os.path.exists(lib):
        return lib
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".forge-trace.",
                               suffix=".so")
    os.close(fd)
    got = run(["cc", *CFLAGS, "-o", tmp, source, "-ldl"])
    if got.returncode != 0:
        os.unlink(tmp)
        return None
    os.replace(tmp, lib)
    return lib


def preload_env(env, lib, record_path):
    """`env` with `lib` first in LD_PRELOAD and the record named."""
    env = dict(env)
    rest = env.get("LD_PRELOAD", "")
    env["LD_PRELOAD"] = lib + (":" + rest if rest else "")
    env[TRACE_VAR] = record_path
    return env


def strace_prefix(record_path):
    """What runs a step under strace, writing its file calls to
    `record_path`, descriptors shown as paths and strings unabridged."""
    return ["strace", "-f", "-qq", "-y", "-s", "4096", "-e", "trace=%file",
            "-o", record_path, "--"]


def read_preload(path):
    """[(kind, path)] from the library's record; nothing if it is
    missing."""
    found = []
    try:
        with open(path, errors="replace") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if len(line) > 2 and line[0] in "xo" and line[1] == " ":
                    found.append((line[0], line[2:]))
    except OSError:
        pass
    return found


# `1234 openat(AT_FDCWD</dir>, "path", ...` or `execve("path", ...`: the
# call, then a descriptor that -y shows as a path, then the path.
STRACE_LINE = re.compile(
    r'^(?:\[pid\s+)?\d*\]?\s*(\w+)\('
    r'(?:(?:AT_FDCWD|-?\d+)(?:<([^>]*)>)?,\s*)?"((?:[^"\\]|\\.)*)"')
STRACE_EXEC = ("execve", "execveat")


def _unescape(text):
    return text.encode("latin-1", "backslashreplace").decode(
        "unicode_escape", "replace")


def read_strace(path, cwd):
    """[(kind, path)] from strace's output. A path relative to the
    working directory is taken as relative to `cwd`, since strace
    doesn't follow chdir: an approximation of the optional tracer."""
    found = []
    try:
        with open(path, errors="replace") as handle:
            for line in handle:
                got = STRACE_LINE.match(line)
                if not got:
                    continue
                call, base, name = got.groups()
                name = _unescape(name)
                if not name:
                    continue
                if not name.startswith("/"):
                    name = os.path.join(base or cwd, name)
                found.append(("x" if call in STRACE_EXEC else "o", name))
    except OSError:
        pass
    return found


def plausible(entries, program, path=None):
    """Whether a step's trace is believable: its own program (`program`,
    the step's argv[0], looked up on `path` and its links followed, as
    /bin/sh is dash) in what was executed or opened. The library
    misses a statically linked program, and an empty trace is never
    believable."""
    names = {os.path.basename(program)}
    found = shutil.which(program, path=path)
    if found:
        names.add(os.path.basename(os.path.realpath(found)))
    return any(os.path.basename(p) in names for _, p in entries)


def relative(entries, wt, exclude=()):
    """The sorted paths in `entries` inside the worktree `wt`, relative
    to it, less .git and anything under `exclude` (relative paths of
    build caches inside it)."""
    roots = {os.path.normpath(wt), os.path.realpath(wt)}
    skip = tuple(os.path.normpath(e) for e in exclude) + (".git",)
    found = set()
    for _, path in entries:
        path = os.path.normpath(path)
        for root in roots:
            if path == root:
                found.add(".")
                break
            if path.startswith(root + os.sep):
                rel = path[len(root) + 1:]
                if not any(rel == s or rel.startswith(s + os.sep)
                           for s in skip):
                    found.add(rel)
                break
    return sorted(found)


def directories(paths, wt):
    """The directories a step read from or listed: each path's parent,
    and each path that is a directory in `wt` now. "." is the top."""
    dirs = set()
    for path in paths:
        if path != "." and os.path.isdir(os.path.join(wt, path)):
            dirs.add(path)
        parent = os.path.dirname(path)
        dirs.add(parent or ".")
    return sorted(dirs)


def records_dir(state_dir):
    return os.path.join(state_dir, RECORDS)


def write_record(state_dir, tree, commit, steps, at, tracer, reads, dirs):
    """Writes depends/<tree>.json, whole and renamed into place; its
    path. `reads` and `dirs` map each step to its paths."""
    folder = records_dir(state_dir)
    os.makedirs(folder, exist_ok=True)
    record = {"tree": tree, "commit": commit, "steps": steps, "at": at,
              "tracer": tracer, "reads": reads, "dirs": dirs}
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".record.")
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    path = os.path.join(folder, tree + ".json")
    os.replace(tmp, path)
    return path


def load_records(state_dir):
    """[(path, record)], newest first; unreadable ones are left out."""
    folder = records_dir(state_dir)
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path) as handle:
                record = json.load(handle)
            if isinstance(record.get("reads"), dict) \
                    and isinstance(record.get("commit"), str) \
                    and isinstance(record.get("at"), str):
                found.append((path, record))
        except (OSError, ValueError, AttributeError):
            continue
    found.sort(key=lambda pr: pr[1]["at"], reverse=True)
    return found


def prune_records(state_dir, when, parse):
    """Drops records older than KEEP, keeping the newest whatever its
    age. `parse` reads a record's `at`."""
    records = load_records(state_dir)
    for path, record in records[1:]:
        try:
            old = when - parse(record["at"]) > KEEP
        except (TypeError, ValueError):
            old = True
        if old:
            try:
                os.unlink(path)
            except OSError:
                pass


def changes(diff_text):
    """(changed, touched) from `git diff --name-status --no-renames`:
    every path that changed, and those added or deleted, which change
    what a directory lists."""
    changed, touched = set(), set()
    for line in diff_text.splitlines():
        status, _, path = line.partition("\t")
        if not path:
            continue
        changed.add(path)
        if status[:1] in ("A", "D"):
            touched.add(path)
    return changed, touched


def why_runs(name, record, changed, touched):
    """Why step `name` must run for this change, or None if nothing it
    depends on changed: the first changed path it read (or one under a
    changed directory), or the first path added or deleted in a
    directory it read from or listed."""
    reads = set(record["reads"].get(name, ()))
    for path in sorted(changed):
        if path in reads or any(r.startswith(path + "/") for r in reads):
            return path
    dirs = set(record.get("dirs", {}).get(name, ()))
    for path in sorted(touched):
        parent = os.path.dirname(path) or "."
        if parent in dirs:
            return "%s (in %s)" % (path, parent if parent != "." else "the top")
    return None


def select(names, always, record, changed, touched):
    """({step: why} for the steps to run, [steps skipped]) for a change,
    given the record. A step marked always runs; one the record doesn't
    know (added since) runs."""
    ran, skipped = {}, []
    for name in names:
        if name in always:
            ran[name] = "always"
        elif name not in record["reads"]:
            ran[name] = "not in the record"
        else:
            why = why_runs(name, record, changed, touched)
            if why is None:
                skipped.append(name)
            else:
                ran[name] = why
    return ran, skipped


def have_strace():
    return shutil.which("strace") is not None
