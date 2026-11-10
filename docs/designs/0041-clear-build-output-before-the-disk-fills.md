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

### Live caches

Added 2026-11-07. Rule 1 keeps any cache whose worktree has a process
in it, and a long-running agent's session always has one, so the cache
that is growing is the one the pressure pass can't reach. On one host
the filesystem filled in seven hours with one `target/` at 761 GB, and
the pass saw 87% and cleared nothing.

`--live-trim-hours H`, off unless given, needs `--high-water`. If the
pressure pass leaves a root at or over the mark, the caches of
worktrees in use are trimmed, the one with most to give first, until
the root is under `--low-water`. A trim takes only the files under
`deps` directories, and the whole sessions under `incremental` ones,
neither read nor written for H hours and not open, so it leaves what
current builds use: most of the bulk is earlier copies of re-linked
test binaries. Rule 2 stands: every Cargo lock is held for the trim, or
the cache is left alone. A file wrongly taken costs a rebuild, since
Cargo treats a missing output as stale.

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

## Amendment, 2026-11-08: shared caches one level down, and trimmed live

Section 3 finds a shared cache only as a direct subdirectory of
`--shared` with a tag at its top. On 2026-11-08 one host's filesystem
filled to 100% while the pass ran every five minutes, saw 99–100%, and
cleared nothing. It saw five caches and kept them all, because each was
busy or had been used within the hour. Most of the space was in places
section 3 doesn't reach:

* **Per-kind targets.** An agent built into `<agents>/.tgt-<name>-a/`
  with one target directory per kind of build beneath it
  (`wasm32-wasip1/`, `vet-windows/` and so on). Cargo tags each of
  those, but not their parent, so the parent isn't a cache and the
  tagged ones are a level too deep. That was about 180 GB on that host.
* **A busy shared cache.** One shared target in use held 150 GB, most
  of it stale `incremental` sessions. The live trim of 2026-11-07 only
  reaches worktrees' caches, so nothing could take any of it.

So, in place of section 3's "direct subdirectories":

1. **A shared cache is any tagged directory under `--shared`, down to
   the same depth as in a worktree** (three levels). A tagged directory
   is one cache and isn't searched further. A directory with a `.git` is
   a worktree, and nothing inside it counts as a shared cache: `--root`
   judges it.
2. **"In use" counts a `CARGO_TARGET_DIR` at or above the cache**, as
   well as one inside it. A process building into `.tgt-x` uses
   `.tgt-x/wasm32-wasip1`.
3. **A shared cache in use is trimmed by `--live-trim-hours`**, under
   the same rules as a live worktree's cache: only stale `deps` files
   and `incremental` sessions, not open, with every Cargo lock held.
4. **One `--shared` inside another**, such as `$HOME` and
   `$HOME/agents`, judges each cache once.

The safety line is unchanged: nothing without a valid `CACHEDIR.TAG` is
ever cleared, and nothing inside a worktree is judged as shared. A cron
line that already gives `--shared` gets the deeper search with the next
pin. `--live-trim-hours` stays off unless the line gives it.

## Second amendment, 2026-11-10: untagged targets, a floor that gives, and an alert

* Status: approved (Alex Bligh, 2026-11-10, in the terminal: "Yes, please do all this"; https://github.com/abligh/agent-forge/issues/265)

On 2026-11-10 one host's filesystem reached 0 bytes free while the
pass ran every five minutes under pressure. It cleared one cache and
kept 31, and builds failed until agents deleted about 700 GB by hand.

* **Untagged target roots.** Fourteen target directories had no tag at
  their root but tagged subdirectories (`vet-wasm/`, `wasm32-*/`).
  Cargo tags only a target directory it creates itself, so when a
  nested build or a tool creates `target/` first, the host's `debug/`
  and `release/` beneath it were never seen: about 458 GB.
* **The floor.** Every spare cache used within
  `--pressure-floor-hours` was kept, silently, and the live trim only
  reaches busy caches, so nothing could touch those even at 100%.

So:

1. **A Cargo profile directory is a cache.** In a directory with no
   tag, a `debug/` or `release/` holding `.fingerprint/` or
   `.cargo-lock` (which only Cargo writes) is judged as a cache of its
   own, by the same rules as a tagged one. The untagged parent and
   anything else in it are never touched.
2. **The floor is logged, and gives way last.** Each cache the floor
   keeps is logged. The live trim (`--live-trim-hours`) also takes
   their stale outputs. If the filesystem is still at `--high-water`
   after that, they are cleared too, least recently used first, until
   under `--low-water`. Rules 1 and 2 still hold: nothing busy or
   locked is cleared.
3. **An alert.** With `--high-water`, a filesystem still at least
   `--alert-percent` (default 97) used after the run is reported on
   standard error, and, with `--alert-to`, told through the relay, at
   most once an hour.

The safety line changes from "a valid `CACHEDIR.TAG`" to "a valid
`CACHEDIR.TAG`, or a Cargo profile directory": both are output only a
build writes, so a wrong decision still costs a rebuild.
