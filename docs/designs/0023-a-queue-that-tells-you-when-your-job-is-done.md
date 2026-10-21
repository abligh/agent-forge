# 0023: A queue that tells you when your job is done

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-21

## Summary

Agents on a shared host take turns at slow work (a full gate run, a
timing, a turn on a shared machine) by posting their place in a chat
room and starting "on X's end post". Each then watches the room until
its turn comes, often for an hour or more, instead of doing something
else. The owner's rule is now the opposite: start anything slow in the
background and take the next item at once. That rule needs a tool,
because today the only way to keep your place in the queue is to keep
watching it.

This design adds `forge-queue`:
- `forge-queue submit` freezes what the job works on (a detached
  worktree at a commit), queues the job, and returns a ticket at once;
- the job waits its turn and runs in its own systemd user unit, so it
  outlives the session that submitted it;
- when it ends, the agent is **sent a message** through the relay, as
  `forge-router` wakes it: ticket, exit status, how long it waited and
  ran, and where its log is. The message also wakes an idle session.
- `forge-queue list` shows what runs and waits, replacing the room's
  queue posts.

A project names its queues (how many jobs each runs at once) in its
configuration. A job is any command, so a queue for timings on another
host is the same tool running the project's own client for that host.

## Evidence

On one shared host, in one morning:
- **Queue posts:** more than forty room messages were agents announcing
  a place in a queue, correcting it, or handing the next turn on ("X,
  your chain is next"). The order lived only in the room.
- **Waiting instead of working:** reading sessions, the owner found
  agents that queued a timing and then waited hours for its turn. A
  turn is announced in the room, so an agent that does other work can
  miss it, and its place goes to the next.
- **Restarts lose waits:** a background task in a session ends when the
  session restarts, so a wait in one is not safe for hours either.
- **Turns that ran long:** one agent's run failed early and nobody
  noticed; the shared machine sat idle under its claim for over three
  hours until a time limit ended it.

## Design

### Submitting

```
forge-queue submit QUEUE [--at REF] [--dir DIR] [--name TEXT] -- COMMAND...
```

* `--at REF` (default `HEAD` of the current worktree) makes a detached
  `git worktree` at that commit under the queue's state directory, and
  runs COMMAND there. The agent's own worktree is free to change at
  once: what runs is what was submitted. A dirty worktree is refused,
  naming the files, unless `--dir` is given instead.
* `--dir DIR` runs COMMAND in DIR as it stands, for jobs whose input is
  already frozen (a packed bundle, a release directory).
* The job is written to `<state>/queue/<QUEUE>/<ticket>.json`: agent,
  session name, command, directory, commit, submit time. The ticket is
  `<queue>-<utc time>-<4 hex>`.
* It is started as `systemd-run --user --unit forge-queue-<ticket>`
  running `forge-queue run <ticket>`, and `submit` prints the ticket
  and returns.

### Waiting and running

`forge-queue run` waits for one of the queue's `slots`, in submit
order: each waiter holds a lock on its own ticket file and takes a slot
only when no older ticket of the same queue is still waiting. While it
waits, it holds nothing else.

With a slot it runs COMMAND through `forge-untimed` (design 0020), so a
quiet hold freezes or stops it like any other untimed work, at the
queue's `nice` and with its `timeout_minutes`. Output goes to
`<state>/queue/<QUEUE>/<ticket>/log`, and `status.json` beside it
records waited, started, ended, exit status, and whether it timed out
or was cancelled. The worktree made by `--at` is removed after the job
unless the queue sets `keep_worktree`.

### Telling the agent

When the job ends, one message goes to the agent's session name (and
its `twins`), through the relay, with the confirmed-delivery path
`forge-router` uses:

```
Queue gate: <name> ended, exit 0 (green), after 12 min waiting and 31 min running.
Ticket gate-20261021T081500Z-3fa2, commit 1a2b3c4d. Log: <path>
```

A failed delivery is retried on the router's reminder interval, and a
ticket whose message never landed is listed by `forge-queue list
--undelivered`. The message is what brings the agent back: the rule
the projects adopt says to come back on it, not to poll.

### Looking and cancelling

* `forge-queue list [QUEUE]`: running and waiting jobs, in order, with
  agent, name, commit, and times; `--done` adds the last day's ended
  ones.
* `forge-queue cancel TICKET`: the submitting agent (or the operator)
  stops a waiting or running job; the agent is still told.
* `forge-queue status TICKET`: one job's `status.json` and the log's
  last lines.

### After a host restart

The units are gone. At its next start (any `forge-queue` call, or a
boot-time `forge-queue recover` unit), a ticket left running is marked
`lost`, and its agent is told so; waiting tickets are started again in
their order. Nothing is re-run without the agent asking.

## Configuration

A `queue` section, inert until present:

| Key | Default | Meaning |
| --- | --- | --- |
| `queues` | `{}` | Queues by name: `slots` (jobs at once, default 1), `nice` (default 10), `timeout_minutes` (default 120), `keep_worktree` (default false). |
| `state_dir` | `~/.local/state/agent-forge/queue` | Where tickets, logs and frozen worktrees live. |
| `retain_days` | 7 | Ended tickets and their logs are pruned after this. |

Messages use the existing `relay` section; agents and their session
names come from `agents` and `twins`.

## Limits

* **It orders only what is submitted to it.** Work an agent starts by
  hand is outside the queue; the projects' rule is what makes agents
  use it.
* **A slot is a count, not a measurement.** A queue does not stop
  other load on the host; timings still need a quiet host (0020) or a
  host of their own, where a queue with one slot is the turn-taking.
* **At-least-once messages.** A delivery the relay took but the agent's
  session lost is not resent; `status` still has the result.
* **Frozen worktrees cost disk** for as long as the job waits;
  `retain_days` and job completion bound it.

## Testing

* Unit tests: ticket ordering with several waiters and slots, refusal
  of a dirty worktree, `--at` freezing (a commit made after submit is
  not in the job), timeout and cancel, `lost` after a simulated
  restart, the message text, retry of an undelivered message.
* An integration test runs three jobs in a one-slot queue under a real
  systemd user manager and checks order, logs and the three messages
  (the relay faked).

## Rollout

1. `forge-queue` with tests, inert without a `queue` section.
2. A project adds one queue (`gate`, one or two slots) and its working
   rule; agents submit gate runs there instead of posting in the room.
3. A project with a timing host adds a one-slot queue whose command is
   its own client for that host, so timings are submitted, frozen and
   reported the same way.
