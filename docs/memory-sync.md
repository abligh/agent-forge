# Shared agent memory across hosts

**When a project's agents run on several hosts, each host's memory
directory is the working tree of one private repository, and
`forge-memory-sync` keeps the hosts in step.** Each run commits what
changed on the host, rebases onto what the other hosts pushed, and
pushes. `MEMORY.md` merges as a union. Any other conflict stops that
host's sync until a person or agent resolves it. Nothing is ever
resolved by guessing.

Two tools look after the index itself: `forge-memory-check` asserts it
still loads in full, and `forge-memory-trim` shortens its lines without
losing anything.

The configuration's `memory` section ([config.md](config.md)) says
where everything is. The examples here use its defaults for a project
called `widget`.

## How a sync works

Claude Code loads a session's memories from
`~/.claude/projects/<slug>/memory`, one directory per host: the
configuration's `memory.dir`. Its git directory is `memory.git_dir`
(default `~/.widget-memory.git`), outside it, so no `.git` appears
beside the memories. The one file the repository adds there is
`.gitattributes`, which holds `MEMORY.md merge=union`.
`forge-memory-check` reads only `*.md` files, so it doesn't see it.

A run:

1. commits every change in the directory, deletions included, with the
   host's name in the message. A memory removed as wrong on one host is
   removed on all of them;
2. fetches `origin` and rebases onto it. Changes to different files
   merge as usual, and `MEMORY.md` keeps both hosts' lines even where
   they touch the same place;
3. pushes. If another host pushed in between, it rebases and pushes
   once more. A second refusal is left to the next run.

If the rebase conflicts, it is aborted. That restores the working tree
exactly, and the local commit is kept, so nothing written on this host
is lost. The run exits 1, and on every later run the host goes on
committing locally and stopping at the same conflict until someone
resolves it (see [When a sync stops](#when-a-sync-stops)).

Every git command is bounded (two minutes), so a network that stops
answering can't hold the job, or its cron lock, indefinitely.

## Authentication

Whatever the host's git uses for `memory.remote`, with two additions.

* **Over SSH**, `memory.ssh_key` (or `FORGE_MEMORY_SSH_KEY` on one
  host) names a private key used for this repository alone, passed as
  `core.sshCommand` with `IdentitiesOnly=yes`. A deploy key is the
  usual choice. GitHub lets a deploy key serve one repository only, so
  a host that already reaches the project with a deploy key needs a
  second key for the memory repository.
* **Over HTTPS**, git asks a credential helper given with `-c`, which
  reads `GH_TOKEN` from the environment when git asks.
  `forge-cron-run` puts an App's installation token there when
  `FORGE_CRON_APP` names it. Any helper configured for the user is
  cleared first, so no `store` helper can write the token to disk. The
  token appears in no argument list, file or log.

## Reference

### Invocation

    forge-memory-sync [--seed] [--file]

| Flag | Effect |
| --- | --- |
| (none) | One sync, as above. |
| `--seed` | Set this host up once. See [Seeding a host](#seeding-a-host). |
| `--file` | Report a run that exits 1 on a GitHub issue in the project's repository, and forget the report once a run exits 0. |

Run it from cron under `forge-cron-run`, which finds the configuration
in the project's cron checkout:

    */10 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-memory-sync --file

Stagger the minute between hosts (`*/10` on one, `5-59/10` on the
next), so they don't push in the same minute. `--file` needs a token
that can write issues: `FORGE_CRON_APP=<machinery App>` on a host that
has the App's key, or `GH_TOKEN`. Without one the run still syncs, and
says it could not report.

### Exit status

| Status | Meaning |
| --- | --- |
| 0 | Synced, or there was nothing to do. |
| 1 | Something needs a person: a conflict, a push refused twice, a rebase already in progress, or a seed that set files aside. |
| 2 | The run couldn't ask its question: no configuration, no memory directory or no `MEMORY.md`, a host not seeded, git or the network failing, or an unexpected error. |

### On-disk state

| Path | Written by | What it is |
| --- | --- | --- |
| `memory.git_dir` | `--seed`, then each run | The repository's git directory. `core.worktree` names the memory directory. |
| `.gitattributes` in the memory directory | `--seed`, on the first host | `MEMORY.md merge=union`. |
| `memory.seed_dir` | `--seed` | A host's own copy of a topic file it had before seeding, which the repository's version replaced. |
| `~/.local/state/agent-forge/<project>/memory-sync/filed.json` | `--file` | The message last reported on the issue, so an unchanged stop isn't reported twice. |

### The issue

With `--file`, a run that exits 1 files an issue titled
`forge-memory-sync: <host> has stopped syncing`, with `memory.labels`,
if none is open. While one is open it comments only when the message
changes. It never closes the issue. A run that exits 0 forgets the
message, so the next stop is reported even if it is the same one.

## Seeding a host

Seeding happens once per host, after the host can reach the repository.

1. On the first host, with the repository empty:

       cd ~/widget-cron && forge-memory-sync --seed

   It commits the directory as it is, with `.gitattributes`, and
   pushes.
2. On each later host, the same command. It merges what the host
   already has into the repository, without overwriting anything:
   * a topic file the repository lacks is committed;
   * an identical file is left alone;
   * a topic file with the same name and different contents is moved
     to `memory.seed_dir`, the repository's copy is checked out, and
     the file is listed. The run exits 1, and with `--file` the list is
     reported on the issue;
   * `MEMORY.md` becomes the repository's lines followed by this host's
     lines that it doesn't already have.
3. Check it worked: `git --git-dir ~/.widget-memory.git log --oneline
   -1` names this host's seed commit, or the repository's latest if the
   host had nothing new, and `git --git-dir ~/.widget-memory.git
   status` is clean.

A seed that fails before it has merged anything, as a first host's does
when its push is refused, removes what it created, and the same command
can be run again. One that fails later leaves its merge committed, and
the next sync pushes it.

A file listed in step 2 is reconciled by whoever wrote it. Compare it
with the checked-out copy, write what both meant into the memory
directory, and the next sync pushes it.

## When a sync stops

**Symptom:** the run exits 1 with `conflict with the other hosts in:
<files>`, or an issue `forge-memory-sync: <host> has stopped syncing`
is open.

**Confirm:** on that host,

    git --git-dir ~/.widget-memory.git diff HEAD origin/main -- <file>

shows both hosts' versions of each file named. There's no need to fetch
first: every run fetches before it rebases, so `origin/main` is at most
one run old.

**Resolve:** in the memory directory,

    git --git-dir ~/.widget-memory.git rebase origin/main
    # edit each conflicting file so it says what both hosts meant
    git --git-dir ~/.widget-memory.git add <file>
    git --git-dir ~/.widget-memory.git -c user.name=forge-memory-sync \
        -c user.email=forge-memory-sync@<host> rebase --continue

The memory repository has no committer identity of its own: the sync
passes `forge-memory-sync` with `-c` on every run. So `--continue`
needs the same pair, or it fails with "Committer identity unknown".
Pass it there rather than setting one on the repository, so a
resolution commits as the sync would.

While the rebase is in progress, a scheduled run sees it, exits 1 and
touches nothing, so it can't commit a half-resolved file. The next run
after `--continue` pushes. Close the issue once it has.

**If that did not work:** `git --git-dir ~/.widget-memory.git rebase
--abort` puts everything back as it was before the resolution started.
The host's own memories are all still in its local commits, and `git
--git-dir ~/.widget-memory.git log origin/main..HEAD` lists them.

**Other stops:**

* `push refused twice`: another host pushed twice while this run
  rebased. The next run retries, and nothing needs doing unless it
  repeats.
* `a rebase is in progress`: someone is resolving a conflict here, or
  left one half done. Finish or abort it as above.

## Keeping the index loadable

`MEMORY.md` is read into every session at start, and past the
harness's limit it is truncated, not refused: the newest entries are
dropped, silently. `forge-memory-check` exits 1 when it is over
`memory.limit`, when an index entry points at no file, or when a topic
file is in no entry (written, and never loaded). It warns, without
failing, near the limit and for each line over `memory.long_line`
bytes. Run it from cron beside the sync:

    0 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-memory-check

The repair it names is `forge-memory-trim`. It lists index lines whose
appended `**Added <date>**: ...` clause is already in the topic file the
line points at, refuses any whose substance isn't, and with `--apply
--entry <file>` removes the named lines' clauses. There is no bulk
mode: whether the shorter line still tells a reader to open the file is
a judgement it leaves to whoever reads it.
