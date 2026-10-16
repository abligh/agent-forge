# 0015: Install a new release beside the running one, never under it

* Status: approved (Alex, 2026-10-16, https://github.com/abligh/agent-forge/issues/24#issuecomment-6210941989)
* Implemented: v0.4.66, off until a host runs `forge-install --versions`.
* Author: XMPPEngineer
* Date: 2026-10-16

## Summary

`forge-install` moves the installation in place, with `git checkout`.
A job must never run part on one release and part on another, so every
job holds the installation's lock shared, and the installer takes it
exclusively. To get it on a host whose checks never all stop at once,
the installer first stops new jobs starting (`INSTALL-PENDING`) and
then waits for the running ones to finish.

The longest running job is a build-check worker, minutes long. So every
pin bump stops the Merger, the Router and new checks until the slowest
check finishes. On one host, a pin bump at 05:35 installed at 05:41,
after two four-minute waits that timed out, and nothing merged in
between. A project that bumps its pin a few times a day pays that each
time.

With `forge-install --versions`, each release gets a directory of its
own, and the installation becomes a symlink to the current one. Moving
to a new release is a rename of that symlink: nothing waits, and a
running job keeps the release it started on.

## Design

**Layout.** The installation path `P` (say
`~/.local/share/agent-forge`) is a symlink to `agent-forge.d/<sha>`,
beside it. Each `P.d/<sha>` is a clean, detached clone at that commit.
Everything that already points at `P`, such as the `~/.local/bin`
links, still does.

**A job runs on one release.** Each tool finds its own code from
`realpath(__file__)`, so a job started through `P` resolves to
`P.d/<sha>` at once and stays there. Two places still resolved through
`P` while running, and both change:

* `forge-cron-run` puts the job's own resolved `bin` first in `PATH`,
  so a tool a job runs by name (a gate's `forge-commit-identity`)
  comes from the same release;
* `forge-pr-check` starts its worker from its own resolved path.

**Installing.** When the pin is not the release `P` points at:

1. Make `P.d/<pin>`, unless a clean clone at the pin is already there:
   clone it beside, as `P.d/<pin>.new`, from the current release (a
   local clone, so the objects are hard links and it takes a second),
   point `origin` at the real URL, fetch the pin and check it out, then
   rename it into place. A half-made clone is never a release.
2. Point `P` at it: write the symlink as `P.new` and rename it over
   `P`, which is atomic. A job that starts after this runs the new
   release; one already running carries on with its own.
3. Mark the release `P` pointed at before as retired
   (`P.d/<sha>.retired`, holding the time).

No step waits for a job, and there is no `install-wanted` marker in
this layout, so `INSTALL-PENDING` never happens.

**Locks.** A job holds the lock of the release it runs (`P.d/<sha>.lock`,
which is `<its install>.lock` as now) shared, for its whole run and
whatever it hands the lock to, as now.

**Pruning.** At each run, `forge-install` deletes a retired release once
it has been retired for ten minutes and its lock can be taken
exclusively, without waiting. The ten minutes cover a job that resolved
the old release just before the switch but hadn't yet taken its lock.
A release still in use is kept until its jobs finish; the current one
is never deleted.

**Moving an existing installation.** The first `--versions` run on a
host whose `P` is a directory moves it once, the old way: it leaves the
marker, waits for the jobs to drain, renames the directory to
`P.d/<its sha>`, puts the symlink in its place and removes the marker.
From then on, no install waits.

**Run it every minute.** After a pin merges, each job refuses
(`PIN-MISMATCH`) until the installer has run. A run that finds the
installation at the pin only reads a symlink and a pin file, so the
cron line can be `* * * * *` rather than every five minutes, and a
bump costs at most a minute or so.

## Trade-offs

* **An old release runs on after a bump**, until its jobs finish, for
  a build check up to its own timeout. Each job is still one release
  from start to finish, which is the property the lock was for.
* **Disk**: one release is about 16 MB, and two or three exist briefly
  after a bump.
* **A job that resolved the old release just before the switch** runs
  it, which is no different from a job that started a moment earlier.
* **Without `--versions` nothing changes**, so a host moves when its
  operator changes its cron line.

## Non-goals

* Installing ahead of the pin, or from a tag: as now, only the pinned
  SHA is ever checked out.
* Removing the lock: it is still what keeps a release from being
  pruned under its jobs.
