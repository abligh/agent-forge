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

The lines a tracer writes are (kind, absolute path): "x" for a
program executed, "w" for a file opened to be written or created, "c"
for a path a directory, link or rename created, "o" for anything else.
A step reads every path but those only created ("c"); it writes, its
*write set* (docs/designs/0018), those opened to write and those
created.
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
# The kinds of line a step read, and those it wrote.
READ_KINDS = ("x", "o", "w")
WRITE_KINDS = ("w", "c")
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
                if len(line) > 2 and line[0] in "xowc" and line[1] == " ":
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
# A quoted path, after any descriptor it is relative to.
STRACE_PATH = re.compile(
    r'(?:(?:AT_FDCWD|-?\d+)(?:<([^>]*)>)?,\s*)?"((?:[^"\\]|\\.)*)"')
# Calls whose flags say whether they write, and one that always does.
STRACE_OPEN = ("open", "openat", "openat2")
STRACE_CREAT = ("creat",)
STRACE_WRITE_FLAGS = re.compile(r"\bO_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)\b")
# Calls that create their (first) path, and those that create their last.
STRACE_MKDIR = ("mkdir", "mkdirat")
STRACE_TO = ("rename", "renameat", "renameat2", "link", "linkat", "symlink",
             "symlinkat")
# A call's result: "= 0" when it succeeded.
STRACE_OK = re.compile(r"\)\s+=\s+0(?:\s|$)")


def _unescape(text):
    return text.encode("latin-1", "backslashreplace").decode(
        "unicode_escape", "replace")


def read_strace(path, cwd):
    """[(kind, path)] from strace's output. A path relative to the
    working directory is taken as relative to `cwd`, since strace
    doesn't follow chdir: an approximation of the optional tracer.

    Each call's first path is read ("o"), or written ("w") for an open
    that may write; a directory, link or rename that succeeded also
    gives the path it created ("c")."""
    found = []

    def absolute(base, name):
        name = _unescape(name)
        if name and not name.startswith("/"):
            name = os.path.join(base or cwd, name)
        return name
    try:
        with open(path, errors="replace") as handle:
            for line in handle:
                got = STRACE_LINE.match(line)
                if not got:
                    continue
                call, base, name = got.groups()
                name = absolute(base, name)
                if not name:
                    continue
                args = line[got.end():].split(") = ")[0]
                kind = "x" if call in STRACE_EXEC else \
                    "w" if call in STRACE_CREAT or (
                        call in STRACE_OPEN
                        and STRACE_WRITE_FLAGS.search(args)) else "o"
                found.append((kind, name))
                if not STRACE_OK.search(line):
                    continue
                if call in STRACE_MKDIR:
                    found.append(("c", name))
                elif call in STRACE_TO:
                    paths = STRACE_PATH.findall(line[got.start(3) - 1:])
                    target = absolute(*paths[-1]) if len(paths) > 1 else ""
                    if target:
                        found.append(("c", target))
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


def relative(entries, wt, exclude=(), kinds=READ_KINDS):
    """The sorted paths in `entries` inside the worktree `wt`, relative
    to it, less .git and anything under `exclude` (relative paths of
    build caches inside it): those a step read, or with `kinds` those
    of other kinds."""
    roots = {os.path.normpath(wt), os.path.realpath(wt)}
    skip = tuple(os.path.normpath(e) for e in exclude) + (".git",)
    found = set()
    for kind, path in entries:
        if kind not in kinds:
            continue
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


def written(entries, wt, exclude=()):
    """The sorted paths in `entries` a step wrote or created inside the
    worktree, as `relative` gives them: its write set
    (docs/designs/0018)."""
    return relative(entries, wt, exclude, WRITE_KINDS)


def outputs_index(writes):
    """(paths, directories above them) of a step's write set and
    declared outputs, for `output_of`."""
    paths, above = set(), set()
    for path in writes:
        path = path.strip("/")
        paths.add(path)
        while "/" in path:
            path = path.rpartition("/")[0]
            above.add(path)
    return paths, above


def output_of(path, index):
    """Whether `path`, missing from the tree checked, is what a step
    left behind, given `outputs_index` of its write set and declared
    outputs: one of them, beneath one (a directory it created or
    renamed into place), or a directory with one beneath it."""
    paths, above = index
    if path in paths or path in above:
        return True
    while "/" in path:
        path = path.rpartition("/")[0]
        if path in paths:
            return True
    return False


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


def write_record(state_dir, tree, commit, steps, at, tracer, reads, dirs,
                 source=None, sources=None, name=None, writes=None):
    """Writes depends/<tree>.json (or `name`), whole and renamed into
    place; its path. `reads` and `dirs` map each step to its paths,
    and `writes`, when given, to its write set (docs/designs/0018),
    which may lack steps.
    `source` is "run" for a record a pull request's run wrote
    (designs/0010), which may lack steps; `sources` says, by step,
    where each one's paths came from. A default-branch run's record
    has neither."""
    folder = records_dir(state_dir)
    os.makedirs(folder, exist_ok=True)
    record = {"tree": tree, "commit": commit, "steps": steps, "at": at,
              "tracer": tracer, "reads": reads, "dirs": dirs}
    if writes is not None:
        record["writes"] = writes
    if source is not None:
        record["source"] = source
    if sources is not None:
        record["from"] = sources
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".record.")
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    path = os.path.join(folder, name or tree + ".json")
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


def prune_records(state_dir, when, parse, per_step=None):
    """Drops records older than KEEP, keeping the newest whatever its
    age. `parse` reads a record's `at`. With `per_step`, a record is
    also dropped unless it is one of the newest `per_step` that have
    one of its steps (designs/0010: every run writes one)."""
    records = load_records(state_dir)
    wanted = None
    if per_step is not None:
        wanted, counts = set(), {}
        for path, record in records:
            for name in record["reads"]:
                if counts.get(name, 0) < per_step:
                    counts[name] = counts.get(name, 0) + 1
                    wanted.add(path)
    for path, record in records[1:]:
        try:
            old = when - parse(record["at"]) > KEEP
        except (TypeError, ValueError):
            old = True
        if old or (wanted is not None and path not in wanted):
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
