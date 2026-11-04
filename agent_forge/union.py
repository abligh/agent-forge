"""The merge of two commits with conflicts of additions resolved
(designs/0046): what the Merger pushes for `merge.resolve.union`, what
its approval check recomputes, and what `forge-resolve` makes for an
author.

The merge is git's own, `git merge-tree --write-tree` of the two
commits, with the conflict style `zdiff3`, so that each conflicting
hunk shows its base. A conflicting hunk is resolved only when its file
matches one of the globs and its base is empty: both sides only added
lines there. Its resolution is the first commit's lines, then the
second's. Any other conflict, in any file, means there is no
resolution.

A glob is matched against the whole path with `fnmatch.fnmatchcase`:
`*` matches any characters, `/` included.
"""

import fnmatch
import os
import re
import tempfile

from agent_forge.proc import sh

# The only kind of conflict that can be resolved: two sides' edits to a
# file's contents. Any other kind (modify/delete, rename, binary, a
# mode or a file/directory clash) is never resolved.
CONTENTS = "CONFLICT (contents)"
REGULAR = ("100644", "100755")
MARK_RE = re.compile(r"^(<{7,}) (.*)$")


class Resolution:
    """What `resolve` found. `tree` is the resolved tree's id, or None
    when the merge can't be resolved, and `reason` then says why.
    `clean` is True when git merged it with no conflict at all. `files`
    is [(path, [(head_lines, main_lines), ...]), ...]: per resolved file,
    in path order, each hunk's added lines from each side."""

    def __init__(self, tree=None, reason=None, clean=False, files=()):
        self.tree, self.reason, self.clean = tree, reason, clean
        self.files = list(files)

    def __bool__(self):
        return self.tree is not None

    def __repr__(self):
        return "Resolution(tree=%r, reason=%r, clean=%r, files=%r)" % (
            self.tree, self.reason, self.clean, self.files)


def globs(merge):
    """`merge.resolve.union` from a configuration's `merge` section, as a
    tuple: () when it is unset, or `resolve` is designs/0026's list."""
    resolve = merge.get("resolve")
    return tuple(resolve.get("union", ())) if isinstance(resolve, dict) \
        else ()


def matches(path, globs):
    """Whether `path` matches one of `globs`."""
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def git(repo, *args, run=sh, env=None):
    return run(["git", "-C", repo] + list(args), env=env)


def parse_merge_tree(output):
    """(tree, {path: {stage: (mode, oid)}}, [(type, [paths])]) from
    `git merge-tree --write-tree -z` with its messages."""
    fields = output.split("\0")
    tree, at, stages = fields[0].strip(), 1, {}
    while at < len(fields) and fields[at]:
        meta, path = fields[at].split("\t", 1)
        mode, oid, stage = meta.split()
        stages.setdefault(path, {})[int(stage)] = (mode, oid)
        at += 1
    at += 1
    messages = []
    while at < len(fields) and fields[at].strip():
        count = int(fields[at])
        paths = fields[at + 1:at + 1 + count]
        kind = fields[at + 1 + count]
        messages.append((kind, paths))
        at += count + 3
    return tree, stages, messages


def union_text(text, head_label, main_label):
    """(resolved text, [(head_lines, main_lines), ...]) for one file as
    `merge-tree` wrote it, conflict markers and all, or (None, why). A
    hunk resolves only when its base section is empty; markers must
    carry the two labels, so a line of the file's own that only looks
    like a marker is never taken for one."""
    out, hunks = [], []
    lines = re.findall(r"[^\n]*\n|[^\n]+", text)
    at = 0
    while at < len(lines):
        bare = lines[at].rstrip("\r\n")
        mark = MARK_RE.match(bare)
        if not mark or mark.group(2) != head_label:
            out.append(lines[at])
            at += 1
            continue
        size = len(mark.group(1))
        base_mark, middle = "|" * size + " ", "=" * size
        end_mark = ">" * size + " " + main_label
        head, main, base = [], [], []
        part = head
        at += 1
        while True:
            if at >= len(lines):
                return None, "a conflict that never ends"
            bare = lines[at].rstrip("\r\n")
            if part is head and (bare.startswith(base_mark)
                                 or bare == base_mark[:-1]):
                part = base
            elif part is head and bare == middle:
                return None, "a conflict that shows no base"
            elif part is base and bare == middle:
                part = main
            elif part is main and bare == end_mark:
                break
            else:
                part.append(lines[at])
            at += 1
        at += 1
        if base:
            return None, "a conflict where a line was changed or removed"
        if not head or not main:
            return None, "a conflict that adds on one side only"
        out += head + main
        hunks.append(([l.rstrip("\r\n") for l in head],
                      [l.rstrip("\r\n") for l in main]))
    if not hunks:
        return None, "no conflict markers"
    for line in out:
        mark = MARK_RE.match(line.rstrip("\r\n"))
        if mark and mark.group(2).startswith(head_label):
            return None, "a conflict marked other than git's own way"
    return "".join(out), hunks


def resolve(repo, head, main, globs, run=sh):
    """The Resolution of merging `main` into `head` (both commit ids in
    `repo`) under `globs`. Writes the resolved files' blobs and the
    tree to the object store, nothing else: no ref, index or worktree
    of `repo` changes."""
    merged = git(repo, "-c", "merge.conflictStyle=zdiff3", "merge-tree",
                 "--write-tree", "-z", "--messages", head, main, run=run)
    if merged.returncode == 0:
        return Resolution(merged.stdout.split("\0")[0].strip(), clean=True)
    if merged.returncode != 1:
        return Resolution(reason="git merge-tree failed: %s"
                          % merged.stderr.strip())
    if not globs:
        return Resolution(reason="no globs are configured")
    tree, stages, messages = parse_merge_tree(merged.stdout)
    for kind, paths in messages:
        if kind.startswith("CONFLICT") and kind != CONTENTS:
            return Resolution(reason="%s in %s" % (kind, ", ".join(paths)))
    if not stages:
        return Resolution(reason="a conflict git names no file for")
    for path in sorted(stages):
        if not matches(path, globs):
            return Resolution(reason="%s conflicts, and no glob names it"
                              % path)
        got = stages[path]
        if set(got) not in ({1, 2, 3}, {2, 3}) or any(
                mode not in REGULAR for mode, _ in got.values()) \
                or got[2][0] != got[3][0]:
            return Resolution(reason="%s conflicts in more than its lines"
                              % path)
    files, blobs = [], {}
    for path in sorted(stages):
        try:
            shown = git(repo, "cat-file", "blob", "%s:%s" % (tree, path),
                        run=run)
        except UnicodeDecodeError:
            return Resolution(reason="%s is not UTF-8 text" % path)
        if shown.returncode != 0:
            return Resolution(reason="git cat-file failed: %s"
                              % shown.stderr.strip())
        size = git(repo, "cat-file", "-s", "%s:%s" % (tree, path), run=run)
        if size.stdout.strip() != str(len(shown.stdout.encode())):
            # Text output turns "\r\n" into "\n": a file with carriage
            # returns can't be read back as it is, so it isn't resolved.
            return Resolution(reason="%s has carriage returns" % path)
        text, hunks = union_text(shown.stdout, head, main)
        if text is None:
            return Resolution(reason="%s: %s" % (path, hunks))
        files.append((path, hunks))
        blobs[path] = text
    resolved = write_tree(repo, tree, stages, blobs, run)
    if resolved.startswith("failed: "):
        return Resolution(reason=resolved[len("failed: "):])
    return Resolution(resolved, files=files)


def write_tree(repo, tree, stages, blobs, run=sh):
    """`tree` with each path in `blobs` given that text, through a
    temporary index: the tree's id, or "failed: why"."""
    with tempfile.TemporaryDirectory(prefix="forge-union-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        got = git(repo, "read-tree", tree, run=run, env=env)
        if got.returncode != 0:
            return "failed: git read-tree: " + got.stderr.strip()
        for path, text in sorted(blobs.items()):
            name = os.path.join(tmp, "blob")
            with open(name, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            made = git(repo, "hash-object", "-w", "--no-filters", name,
                       run=run)
            if made.returncode != 0:
                return "failed: git hash-object: " + made.stderr.strip()
            got = git(repo, "update-index", "--cacheinfo", "%s,%s,%s" % (
                stages[path][2][0], made.stdout.strip(), path),
                run=run, env=env)
            if got.returncode != 0:
                return "failed: git update-index: " + got.stderr.strip()
        got = git(repo, "write-tree", run=run, env=env)
        if got.returncode != 0:
            return "failed: git write-tree: " + got.stderr.strip()
        return got.stdout.strip()


def summary(files, head_name, main_name, limit=20):
    """Markdown listing each resolved file and the lines it took from
    each side, at most `limit` lines a side a hunk."""
    parts = []
    for path, hunks in files:
        for number, (head, main) in enumerate(hunks, 1):
            where = "`%s`" % path
            if len(hunks) > 1:
                where += " (%d of %d)" % (number, len(hunks))
            parts.append("%s: %s from %s, then %s from %s:" % (
                where, lines_word(len(head)), head_name,
                lines_word(len(main)), main_name))
            parts.append(fenced(["+ " + l for l in head[:limit]]
                                + more(head, limit)
                                + ["+ " + l for l in main[:limit]]
                                + more(main, limit)))
    return "\n\n".join(parts)


def lines_word(n):
    return "1 line" if n == 1 else "%d lines" % n


def more(lines, limit):
    return ["  ... and %d more" % (len(lines) - limit)] \
        if len(lines) > limit else []


def fenced(lines):
    """`lines` in a code block whose fence no line can close."""
    longest = max([len(run) for l in lines
                   for run in re.findall(r"`+", l)] + [2])
    fence = "`" * (longest + 1)
    return "\n".join([fence + "diff"] + lines + [fence])
