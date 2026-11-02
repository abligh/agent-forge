# 0041: Clear build output before the disk fills

* Status: approved (Alex Bligh, 2026-11-02, https://github.com/abligh/agent-forge/issues/188#issuecomment-6577187240)
* Implemented: v0.4.145, off until a host's cron line gives the options.
* Author: XMPPEngineer
* Date: 2026-11-02

## Summary

`forge-prune-builds` (designs/0016) runs hourly and clears a
worktree's build output once it has been idle long enough. On busy
hosts the disk now fills before it gets a chance. On one day, one
host's 1.8 TB `/home` filled twice. The first time, an agent's merged
worktrees still held 220 GB. The second time, five minutes after an
hourly run cleared nothing ("cleared 0, kept 27"), about 900 GB of
idle build output could be cleared. Every build on the host failed
with "No space left on device" until agents cleared their own by hand.

Two things let it happen:

1. **The idle rule waits too long while space runs out.** An open pull
   request's worktree is kept for 48 hours after its last use, and
   each holds 25 to 30 GB. One agent had 23 such worktrees, about
   600 GB, none of them in use.
2. **Shared build directories aren't seen at all.** Agents also build
   into directories outside their worktrees, such as
   `<agents>/.tgt-<name>` (Cargo's `CARGO_TARGET_DIR`). The tool only
   looks inside worktrees, so these are never cleared. On that host
   they held 360 GB, one of them 180 GB.

This design adds both, off by default: a disk-pressure mode that
clears the least recently used build output, whatever its age, until
the disk is back under a limit; and shared cache directories as
candidates.

## Design

### 1. Pressure: `--high-water` and `--low-water`

`--high-water PCT` (e.g. 85) and `--low-water PCT` (e.g. 70) are the
filesystem's used percentage, as `df` shows it, for the filesystem each
`--root` is on.

* **Over the high-water mark**, after the normal pass, the tool clears
  more. The candidates are every cache directory that passes
  conditions 1 and 2 of designs/0016 (no process uses it, no Cargo lock
  is held), whatever its idle time. They are cleared one at a time,
  least recently used first, judged by condition 3's newest file time.
  The tool stops once the filesystem is under the low-water mark.
* **One floor stays:** a cache used in the last `--pressure-floor-hours`
  (default 1) is never cleared under pressure. A build that just
  finished and whose agent is reading the results keeps its output.
* **Under the high-water mark**, nothing changes from today.

Each pressure clearing is reported as `pressure: used 9h ago`, with the
used percentage before and after.

Under pressure, a wrong "idle" still costs only a rebuild, as in
designs/0016: only tagged cache directories are deleted. Clearing the
oldest first means the build output most likely to be needed again is
the last to go.

### 2. Checking often, scanning seldom: `--every-minutes`

Space can run out within minutes of an hourly run. With
`--every-minutes N` (e.g. 60), the tool can be run from cron every 5
minutes. A run that finds every root's filesystem under the high-water
mark, with its last full scan less than N minutes ago, exits at once.
That check is one `statvfs` per root. Without the option, every run is
a full scan, as today. The time of the last full scan is kept in the
tool's state directory.

### 3. Shared cache directories: `--shared`

`--shared DIR` (repeatable) names a directory whose direct
subdirectories are build caches shared between worktrees, such as
`<agents>` with its `.tgt-*`. A subdirectory is a candidate only if it
carries a valid `CACHEDIR.TAG` at its top, the same safety line as
today. Cargo writes one into every target directory.

It is judged by conditions 1 to 3 of designs/0016, with these
differences:

* **No pull request decides it.** It belongs to no single branch, so
  only `--idle-hours` applies, or pressure.
* **"In use" also counts a process that builds into it from
  elsewhere.** Condition 1 also counts a process whose environment
  names the directory as `CARGO_TARGET_DIR`. The tool can read that
  from `/proc/<pid>/environ` for its own user's processes, which the
  agents' are. Cargo's locks (condition 2) still guard a build in
  progress.

The same `--root` may be given as `--shared` too: worktrees are found
by their `.git`, shared caches by their tag, and a directory that has
both is treated as a worktree.

### Configuration and rollout

All three options are off unless given, so a host's cron line keeps
today's behaviour until it is changed. A project that wants them
changes its hosts' cron lines (in `host_settings.crontabs` where it
tracks them), for example:

    */5 * * * * … forge-prune-builds --root $HOME/agents --shared $HOME/agents --high-water 85 --low-water 70 --every-minutes 60

## Trade-offs

* **Pressure clears build output agents will want again.** That costs
  rebuilds, 10 to 20 minutes of CPU each. The alternative is a full
  disk, where every build on the host fails until someone notices.
* **Oldest-first is a guess at "least likely to be needed".** It's the
  same evidence designs/0016 uses (file times), and the floor protects
  the builds that just finished.
* **`/proc/<pid>/environ` shows a process's environment at start.** A
  shell that changes `CARGO_TARGET_DIR` later isn't seen, but Cargo
  started after that change is, and Cargo's locks cover the build
  itself.

## Non-goals

* Disk quotas per agent, or telling an agent its share. The tool still
  only clears caches.
* Removing worktrees, branches or anything untagged.
* Changing how agents choose their target directories. That's for each
  project.
