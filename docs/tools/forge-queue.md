# forge-queue

`forge-queue` queues slow work, such as a full gate run or a timing, and
tells the agent who submitted it when it is done
([designs/0023](../designs/0023-a-queue-that-tells-you-when-your-job-is-done.md)).
Agents that share a host take turns at work like this. Rather than keep
a place in a chat room and watch it, an agent submits the job and goes
on with something else. The job is frozen at a commit, waits its turn
in its own systemd user unit (which outlives the agent's session), and
when it ends the agent gets one chat message through the relay, saying
how it ended and where its log is.

## Usage

    forge-queue [--state-dir DIR] submit QUEUE [--at REF | --dir DIR]
                [--name TEXT] [--agent NAME] -- COMMAND...
    forge-queue [--state-dir DIR] list [QUEUE] [--done] [--undelivered]
    forge-queue [--state-dir DIR] status TICKET
    forge-queue [--state-dir DIR] cancel TICKET [--agent NAME] [--operator]
    forge-queue [--state-dir DIR] recover
    forge-queue [--state-dir DIR] run TICKET

An agent runs it by hand, from her worktree:

    $ forge-queue submit gate -- make gate
    gate-20261021T081500Z-3fa2
    $ forge-queue submit timing --dir ~/bundle --name "series x" -- ./time
    $ forge-queue list
    $ forge-queue status gate-20261021T081500Z-3fa2

`--state-dir` comes before the subcommand. Everything after the first
`--` is the command, never options.

`recover` should also run at boot, from a systemd user unit or cron, so
that jobs a restart interrupted are dealt with:

    @reboot FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-queue recover
    */30 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-queue recover

`run` is what a job's systemd unit runs. Nobody runs it by hand.

The host needs a systemd user manager (`systemd-run --user`). From cron,
where `XDG_RUNTIME_DIR` is unset, it is set to `/run/user/<uid>` when
that directory exists.

## Options

| Option or argument | Subcommand | Default | Meaning |
| --- | --- | --- | --- |
| `--state-dir DIR` | all, before the subcommand | `queue.state_dir`, else `~/.local/state/agent-forge/queue` | Where tickets, logs and frozen worktrees live. |
| `QUEUE` | `submit` | required | The queue, one of `queue.queues`. The ticket is printed. |
| `--at REF` | `submit` | `HEAD` | Run at this commit, in a frozen worktree. Refuses a worktree with uncommitted or untracked changes. Not with `--dir`. |
| `--dir DIR` | `submit` | none | Run in DIR as it stands, for input that is already frozen. Not with `--at`. |
| `--name TEXT` | `submit` | the command, first 60 characters | What the job is called in messages. |
| `--agent NAME` | `submit` | `$FORGE_AGENT`, else the worktree's stamped identity | Whose job it is. Must be one of `agents`. |
| `-- COMMAND...` | `submit` | required | The command to run, as an argument list. |
| `QUEUE` | `list` | every queue | The running and waiting jobs of this queue, in order. |
| `--done` | `list` | off | Add the jobs that ended in the last day. |
| `--undelivered` | `list` | off | List only ended jobs whose message hasn't landed. |
| `TICKET` | `status` | required | Print the job's details and status as JSON, then the last 20 lines of its log. |
| `TICKET` | `cancel` | required | Stop a waiting or running job. Its agent is still told. |
| `--agent NAME` | `cancel` | `$FORGE_AGENT`, else the worktree's stamped identity | Who is asking. Only the job's own agent may cancel it. |
| `--operator` | `cancel` | off | Cancel another agent's job. |
| (none) | `recover` | | Mark lost, start again, send messages due, and prune old tickets, over every queue. |
| `TICKET` | `run` | required | Run one ticket: what the job's unit runs. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_AGENT` | The default agent for `submit` and `cancel`. |
| `FORGE_CONFIG` | The configuration file. Otherwise it is the cron checkout's, then the one around the working directory ([config.md, Finding it](../config.md#finding-it)). |
| `FORGE_CRON_CHECKOUT` | The cron checkout whose configuration is trusted, so an agent's branch can't change the queues. |
| `FORGE_PROJECT_ROOT` | Find the configuration, when neither of the above is set. |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the default state directory, as for every tool. |
| `FORGE_ROUTER_RELAY` | The relay's base URL for the end-of-job message, overriding `relay.url`. Taken from the submitter's environment. |
| `XDG_RUNTIME_DIR` | Where `systemd-run --user` finds the user's bus; see Usage. |

A job runs with the submitter's environment, plus:

| Variable | What it is |
| --- | --- |
| `FORGE_QUEUE_TICKET` | The job's ticket. |
| `FORGE_AGENT` | The job's agent. |
| `FORGE_QUEUE_SUMMARY` | A file the job may write to. Its last line (up to 300 characters) is added to the end-of-job message. |

## Configuration

The `queue` section names the queues
([config.md, Keys](../config.md#keys) lists the keys): `queues`, each
with `slots`, `nice`, `timeout_minutes` and `keep_worktree`;
`state_dir`; and `retain_days`. Without the section, nothing can be
submitted. For example:

    "queue": {
      "queues": {"gate": {"slots": 2, "timeout_minutes": 90},
                 "timing": {"nice": 0}}
    }

It also reads `agents` (each agent's session names, `twins` and
stand-in while away, for whom the message goes to) and `relay`
(`url`, `token_file`, `remind_seconds`).

## What it does

**Submitting.** `submit` freezes what the job works on and returns at
once:

* With `--at REF` (the default, `HEAD`), it refuses a worktree with
  changes, naming the files, since they wouldn't be in the job. Then it
  makes a detached `git worktree` at REF's commit inside the ticket's
  directory, and the job runs there, in the same subdirectory it was
  submitted from. The agent's own worktree is free to change at once.
  The frozen worktree is removed when the job ends, unless the queue
  sets `keep_worktree`.
* With `--dir DIR`, the job runs in DIR as it stands.

The ticket is `<queue>-<UTC time>-<4 hex>`. The job is written to
`<state_dir>/<queue>/<ticket>.json`, mode 0600: agent, sessions,
command, directory, commit, submit time, the queue's settings and the
submitter's environment. It is started as
`systemd-run --user --unit forge-queue-<ticket> --collect`, running
`forge-queue run <ticket>`.

**Running.** The runner waits for one of the queue's `slots`, in submit
order. The command runs at the queue's `nice`, in its own process
group, with its output in the ticket's `log`. Past `timeout_minutes`
the group is sent SIGTERM, then SIGKILL 10 seconds later. When the
installation has a `forge-untimed` tool, the command runs through it,
so a quiet hold freezes it like other untimed work. The ticket's state
goes `queued`, `waiting`, `running`, then one of `done`, `timed-out`,
`cancelled`, `lost` or `failed`.

**The message.** When the job ends, one message goes to the agent's
sessions (hers and her twins', or her stand-in's) through the relay,
with the same confirmed delivery as `forge-router`:

    Queue gate: make gate ended, exit 0 (green), after 12 min waiting and 31 min running.
    Ticket gate-20261021T081500Z-3fa2, commit 1a2b3c4d. Log: <path>
    <the last line the job wrote to $FORGE_QUEUE_SUMMARY, if any>

A non-zero exit is `(red)`, as are a kill by a signal and a timeout. A
message the relay didn't take is tried again every
`relay.remind_seconds` (default 7200): by the runner, which stays until
it lands, and, once the runner is gone, by `recover`.

**Listing.** `list` prints one line per job: state, ticket, agent,
name, commit (or directory), and times. An ended job whose message
hasn't landed says `NOT TOLD: <why>`. With nothing to show, it prints
`nothing queued` (or `nothing undelivered`).

**Cancelling.** `cancel` marks the ticket cancelled. A running job's
runner stops it within seconds (`cancelling <ticket>: …`); a waiting
one ends at once (`cancelled <ticket>`). Either way the agent is told.

**Recovering.** Every subcommand but `run` first does what `recover`
does:

* a ticket left `running` with no runner holding its lock (the host
  restarted, or the unit was stopped) is marked `lost`, and its agent is
  told (`lost: <ticket> …`). Nothing that ran is run again;
* a waiting ticket with no runner is started again, in submit order
  (`started again: <ticket>`), unless it was queued in the last two
  minutes and its runner may still be starting;
* messages due again are sent;
* ended tickets older than `queue.retain_days` (default 7) are removed,
  with their logs.

## State and logs

Under the state directory (default `~/.local/state/agent-forge/queue`),
one directory per queue:

| Path | What it is |
| --- | --- |
| `<queue>/<ticket>.json` | The job, mode 0600. |
| `<queue>/<ticket>/log` | The command's output. |
| `<queue>/<ticket>/status.json` | Its state; when it waited, started and ended; its exit status; whether its message was delivered. |
| `<queue>/<ticket>/summary` | `$FORGE_QUEUE_SUMMARY`. |
| `<queue>/<ticket>/worktree` | The frozen worktree, while the job runs (or after, with `keep_worktree`). |
| `<queue>/.slot-<n>`, `<queue>/.lock`, `<queue>/.seq` | Slots, the queue's lock, and the submit order. |

The runner's own output goes to the systemd journal:
`journalctl --user -u forge-queue-<ticket>`.

## Exit status

* **0**: done. For `submit`, the ticket is printed. For `cancel`, the
  job is stopped or will be within seconds.
* **1**: `cancel`: the job had already ended.
* **2**: bad arguments; an unknown queue or ticket; no configuration
  or no `queue` section (for `submit`); an agent that isn't one of
  `agents`; a dirty worktree; or a cancel by someone other than the
  job's agent without `--operator`.
* **3**: `submit`: git or `systemd-run` failed; nothing is queued.

## See also

* [designs/0023](../designs/0023-a-queue-that-tells-you-when-your-job-is-done.md).
* [forge-router](forge-router.md), whose relay delivery it uses.
* [forge-quiet](forge-quiet.md), the quiet holds `forge-untimed` honours.
