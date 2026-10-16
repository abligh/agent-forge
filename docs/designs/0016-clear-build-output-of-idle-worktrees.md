# 0016: Clear the build output of idle worktrees

* Status: proposed
* Implemented: v0.4.68, off until a host runs it.
* Author: XMPPEngineer
* Date: 2026-10-16

## Summary

Agents work in a git worktree per task, and each one builds its own
cache: a Rust `target/` is 10 to 30 GB. Nothing removes it when the
task ends. On one host, 131 such worktrees held 1.3 TB, 1.3 TB of it
build output, and the disk was 89% full. 111 of them were for pull
requests that had already merged.

`forge-prune-builds`, run from cron, clears the build output of a
worktree that nobody is using. It deletes only cache directories, never
source, git data or anything an agent wrote by hand, so the worst case
of a wrong decision is one rebuild.

## Design

**What it may delete.** Only a directory that marks itself as a cache
with a valid `CACHEDIR.TAG` (the Cache Directory Tagging
Specification), at most three levels inside a worktree: a directory
with a `.git` directly under one of the `--root` directories (e.g. the
supervisor's agents directory). Cargo writes that tag into every
`target/`. A directory without it is never touched, whatever its name.

**When.** A cache directory is cleared only if all of these hold:

1. **No process is using the worktree**: none has its working directory
   inside it or a file open there (from `/proc`).
2. **Nothing is building into it**: every lock file Cargo holds while
   building can be taken without waiting. These are `.cargo-lock`, and
   in newer Cargo `.cargo-build-lock` and `.cargo-artifact-lock`, in
   each profile directory (`debug/`, `release/`) and in each target's
   own (`wasm32-unknown-unknown/debug/`). The tool holds them all
   while it renames the directory, so a build that starts meanwhile
   waits for it.
3. **No file in it has been read or written for N hours**, judged by
   the newest atime and mtime of the files. Neither the directories nor
   the tag file count: a scan like this one lists the directories and
   reads the tag, which updates their atimes. On the first host, before
   the tag was left out, every cache looked "used 0h ago". N is:
   * **6 hours** when the worktree's branch is the head of a pull request
     that has merged or closed;
   * **48 hours** otherwise, including when the pull request's state
     can't be read (no configuration, no token, an API failure).

   The state comes from the configuration's repository, read with the
   same read token as forge-config's other tools (`FORGE_AGENT`'s App,
   else `GH_TOKEN`). GitHub is asked only when the answer depends on
   it, i.e. for a cache idle between the two values.

   The two values guard each other. A merged pull request is not proof
   that a worktree is finished: on the first host, 12 worktrees whose
   pull requests had merged were in use that day, reused for follow-up
   work. Hosts usually mount with `relatime`, which updates a file's
   atime at most once a day, so the atime may be up to 24 hours stale.
   The 48-hour rule allows for that. For the 6-hour rule the evidence is
   the mtime plus conditions 1 and 2, and a merged branch's test
   binaries are rarely run again without a rebuild.

**How.** It renames the directory to `<name>.forge-pruning` inside the
same worktree, then deletes that. A tool that starts in the meantime
finds no half-deleted cache, just none at all. A leftover
`.forge-pruning` from an interrupted run is deleted on the next run.

**What it reports.** One line per directory cleared or kept, with the
reason (`busy`, `locked`, `used 3h ago`, `pull request finished, idle
9h`), and a total.
`--dry-run` reports without deleting.

**Running it.** `forge-cron-run forge-prune-builds --root <dir>`
hourly, with `--merged-hours` and `--idle-hours` to change the values.
One scan of a tree this size takes a few minutes, mostly reading file
times. Nothing runs until a host adds the cron line, so it is off by
default.

## Trade-offs

* **A wrong "idle" costs a rebuild.** That's minutes of CPU, never lost
  work, because only tagged cache directories are deleted.
* **The tag is the safety line.** A build system that keeps something
  irreplaceable in a tagged directory would lose it. The specification
  says such a directory must be safe to delete, so that would be the
  tool's bug, not this one's.
* **The worktrees themselves stay.** Removing a worktree can lose
  uncommitted work, and that's the agent's decision.

## Non-goals

* Sharing one Cargo target directory between an agent's worktrees. That
  would save more, but it changes how agents build, so it's for each
  project.
* Removing worktrees, branches or anything outside tagged cache
  directories.
* Disk quotas per agent.
