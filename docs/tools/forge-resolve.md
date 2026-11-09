# forge-resolve

`forge-resolve` merges the default branch into a pull request's branch
exactly as the Merger would under
[`merge.resolve.union`](../config.md#mergeresolveunion). Where both
sides only added lines to a file the project lists (a changelog, a
table of contents), it keeps both: this branch's lines, then the
default branch's. An author runs it when a pull request conflicts with
the default branch. The merge it makes is the one the Merger
recomputes from the merge's two parents, so the approval of the head
before still covers it, as it covers a clean merge, and no new review
is needed.

## Usage

    forge-resolve [--onto REF] [--dry-run]

It is run by hand, by an agent or a person, inside a worktree of the
pull request's branch. It doesn't fetch: fetch the default branch
first.

    git fetch origin
    forge-resolve --dry-run     # say what it would keep
    forge-resolve               # make the merge
    git push

It is not run from cron or by `forge-receiver`.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--onto REF` | `origin/<default_branch>` | The ref to merge into `HEAD`. The default branch's name is read from this checkout's own `.agent-forge/config.json`. |
| `--dry-run` | off | Say what it would keep, and change nothing. The worktree may have changes. |

## Environment

It reads no `FORGE_*` variable. git's own variables apply: the merge
commit is made with git's usual identity (`user.name`, `user.email`,
`GIT_AUTHOR_*`, `GIT_COMMITTER_*`).

## Configuration

Two files are read:

* this checkout's `.agent-forge/config.json`, only for
  `default_branch` ([Keys](../config.md#keys)), and only when `--onto`
  is not given;
* the configuration **at `REF`** (`git show REF:.agent-forge/config.json`),
  for the globs of
  [`merge.resolve.union`](../config.md#mergeresolveunion). This is the
  copy the Merger judges by, so a branch can't widen the globs for
  itself. The file at `REF` must pass the same checks as any loaded
  configuration.

## What it does

1. It finds the top of the git checkout around the working directory.
2. It reads the globs from the configuration at `REF`.
3. If `HEAD` already contains `REF`, it prints `UP-TO-DATE` and stops.
4. If no glob is configured it prints `OFF: merge.resolve.union names
   no files at REF`, and goes on: a merge with no conflict is still
   made.
5. It merges `REF` into `HEAD` with `git merge-tree`, in memory. A
   conflict is resolved only when every conflict is in the lines of a
   file a glob matches (Python's `fnmatch`, so `*` matches `/` too),
   and every conflicting hunk's base is empty: both sides only added
   lines. Each such hunk becomes this branch's lines, then `REF`'s.
   Anything else and it makes nothing, printing `CONFLICT: <reason>;
   merge it by hand (git merge REF)`. Reasons include a conflict in a
   file no glob names, one in more than its lines (a rename,
   modify/delete, binary or mode conflict), a file that isn't UTF-8
   text or has carriage returns, and a hunk where a line was changed or
   removed.
6. It prints, for each resolved file, the lines it kept from each side.
7. With `--dry-run` it prints `WOULD-MERGE: REF into <branch>, keeping
   both sides in <files>` and stops.
8. Otherwise it refuses a worktree with uncommitted changes to tracked
   files (`DIRTY`). It writes the merge commit with `HEAD` as its first
   parent and `REF` as its second, and moves the branch to it with
   `git merge --ff-only`. The message is `Merge REF into <branch>;
   additions kept from both sides in <files>`, or `Merge REF into
   <branch>` for a clean merge. It prints `MERGED: REF into <branch> as
   <sha>`.

A hand-resolved conflict, made with `git merge`, needs a reviewer's
approval as before. Push the branch afterwards; `forge-resolve` never
pushes.

Lines it prints:

| Line | Meaning |
| --- | --- |
| `NO-REPO` | Not inside a git checkout. |
| `NO-CONFIG` | A configuration could not be read or failed its checks. |
| `NO-REF` | `HEAD` or `REF` is not a commit. |
| `UP-TO-DATE` | `HEAD` already holds `REF`. |
| `OFF` | No globs at `REF`; only a clean merge can be made. |
| `CONFLICT` | A conflict it does not resolve, and why. |
| `WOULD-MERGE` | `--dry-run`: the merge it would make. |
| `DIRTY` | Tracked files have uncommitted changes. |
| `FAILED` | `git commit-tree` or `git merge --ff-only` failed. |
| `MERGED` | The merge was made. |

## State and logs

None. It writes only the merge commit and moves the current branch.

## Exit status

* 0: the merge was made, or would be with `--dry-run`, or `HEAD`
  already holds `REF`.
* 1: a conflict it does not resolve; the reason is printed.
* 2: the checkout, the ref or a configuration could not be read, the
  worktree has changes, or git failed.

## See also

* [`merge.resolve.union`](../config.md#mergeresolveunion) and
  [`merge.resolve`](../config.md#mergeresolve) in config.md.
* [designs/0046](../designs/0046-the-merger-resolves-conflicts-of-additions.md),
  the design.
* [forge-merger](forge-merger.md), which makes the same merge itself.
