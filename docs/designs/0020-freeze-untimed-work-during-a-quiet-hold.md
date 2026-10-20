# 0020: Freeze untimed work during a quiet hold

* Status: approved (Alex Bligh, 2026-10-20, https://github.com/abligh/agent-forge/issues/33#issuecomment-6286890539)
* Author: XMPPEngineer
* Date: 2026-10-20

## Summary

A quiet hold (`forge-quiet`) stops the build check, deploys and nightly
jobs from starting, but not the agents' own untimed work: builds, gates
and reviews that they start by hand on the shared host. Today each agent
is asked, in a chat room, to pause or stop its own work when someone
announces a hold, and to start it again afterwards. That depends on
everyone reading the room, and pausing a job by hand is easy to get
wrong.

This design adds `forge-untimed`. It runs a command in its own systemd
user scope, so that `forge-quiet hold` can handle the work before the
holder's measurement starts:
- freeze it, through the kernel's cgroup freezer;
- or let it finish, for test runs that a freeze would break;
- or stop it.

New untimed work that starts while a hold is live waits for the hold to
end. Release or expiry thaws everything.

## Evidence

On one shared host, on one morning:

- **Gates lost:** three agents paused their running gates for a hold
  with `SIGSTOP` and lost them. Stopping a background job's top shell
  ended the whole job, so all three restarted from the beginning, about
  half an hour of builds in all. One of the gates was killed a second
  time for the next hold, because a test step frozen for 20 minutes
  might have failed when resumed.
- **Slots disturbed:** a quiet slot was disturbed by a 70-second
  format-and-doc check, which its agent hadn't stopped, and another by
  review runs that overlapped its start. Both measurements had to be
  discounted.
- **Every hold is coordinated by hand:** before each one, each agent
  with work running posted that it had paused or stopped it, and after
  it, that it had resumed. That's three or four messages per hold, and
  there were about 16 holds that morning.
- **How busy the host is:** over the previous day and a half, the host
  was held quiet for 23% of the time. At least one agent was waiting for
  a slot during 73% of the held time.

## Design

### `forge-untimed`

```
forge-untimed [--on-hold freeze|finish|stop] [--name <label>] -- <command>…
```

**What it does.** It runs `<command>` under
`systemd-run --user --scope --slice=forge-untimed.slice`. The scope is
named `forge-untimed-<agent>-<label>-<n>.scope`, and the agent is
`$FORGE_AGENT`. The command keeps the caller's terminal, environment
and working directory, because a scope, unlike a service, runs in place.
It is started with `nice` 10.

**Before it starts,** it waits while a hold is live, so new untimed
work never starts inside someone's slot. It says why it is waiting, and
`--no-wait` overrides that. Its exit status is the command's own.

**`--on-hold`** says what a hold does to this scope:

| Mode | What a hold does | For |
|---|---|---|
| `freeze` (default) | Freezes the scope's whole process tree (cgroup v2 `cgroup.freeze`, through `systemctl --user freeze`) until the hold ends. | builds, which don't mind a pause |
| `finish` | Waits for the scope to end before the hold settles, for at most `quiet.settle_minutes`. | test runs, which may fail on a timeout or a wall-clock check when resumed |
| `stop` | Stops the scope (SIGTERM, then SIGKILL after 10 s). | work that's cheap to rerun |

**Containers.** A container that a command starts runs in Docker's
cgroup, not in the scope, so freezing the scope would freeze only the
`docker` client. To cover that, `forge-untimed` exports
`FORGE_UNTIMED_SCOPE`, and a project's container wrapper adds
`--label forge-untimed=$FORGE_UNTIMED_SCOPE`. A hold applies the
scope's mode to every running container with that label: `docker pause`
for `freeze`, waiting for `finish`, `docker stop` for `stop`. A
container started without the label is outside this design, as today.

**`forge-untimed list`** shows each scope: its agent, label, mode,
state (running, frozen, waiting) and age.

### What `forge-quiet hold` adds

Once the hold is written, and before the project's `quiet.settle`
command:
1. It freezes every `freeze` scope and pauses their containers.
2. It stops every `stop` scope.
3. It waits for every `finish` scope, reporting what it is waiting for.

The holder's own scopes are skipped, so the holder can keep a build of
its own running.

### Thawing

Thawing has three triggers, so that a holder that dies can't leave work
frozen. Each one thaws only when no hold is live, because another
holder may have taken one meanwhile.
- **On release:** `forge-quiet release` thaws.
- **On expiry:** after settling, `forge-quiet hold` starts a transient
  user timer (`systemd-run --user --on-active=<minutes>m`) that runs
  `forge-quiet thaw` when the hold expires.
- **As a safety net:** `forge-quiet status` thaws whenever it finds no
  live hold, and the build check and the deploy job call it often.

A thawed scope carries on where it was. A `finish` or `stop` scope is
not restarted: rerunning it is its owner's call, and
`forge-untimed list` shows that it ended.

### Configuration

The design adds a `quiet.untimed` section, off by default:

```json
"quiet": {"dir": "...", "untimed": {"enabled": true,
          "default_on_hold": "freeze", "slice": "forge-untimed.slice"}}
```

While it is off:
- `forge-untimed` still runs the command in a scope, and still waits
  for live holds;
- `forge-quiet` touches no scope.

That lets a project adopt the wrapper first and turn on freezing
afterwards.

## Limits

- **Opt-in:** only work started through `forge-untimed` is covered.
  Projects write that into their own rules ("untimed heavy work runs
  under `forge-untimed`"). Anything started otherwise is outside it, as
  today.
- **Memory:** a frozen scope keeps its memory. On a host short of
  memory, `stop` is the better default.
- **Requirements:** systemd ≥ 246 with cgroup v2 and a user manager for
  the agents' account. Checked on one host: systemd 255, in a sysbox
  container, where `systemctl --user freeze` froze a busy loop's scope
  and thawed it.
- **Not isolation:** this keeps untimed work out of a slot, and doesn't
  pin cores. Timing slots on reserved cores are a separate, later
  question for hosts with enough cores to spare.

## Testing

- **Unit tests,** with fake `systemctl` and `docker` runners:
  - a hold freezes, waits for or stops each mode, and skips the
    holder's own scopes;
  - release, expiry and `status` thaw only when no hold is live;
  - `forge-untimed` waits during a hold, and `--no-wait` doesn't;
  - the exit status is the command's own.
- **One real test,** skipped where there's no user manager: start a
  busy loop under `forge-untimed`, take a hold, check that the scope's
  `FreezerState` is `frozen` and that it uses no CPU, release the hold,
  and check that it is `running` again.

## Rollout

1. Implement, off by default, in an agent-forge release.
2. A project adds `forge-untimed` to its rules for heavy untimed work,
   with `quiet.untimed.enabled` left off: that only stops new work
   starting during holds.
3. The project turns freezing on, and stops asking in chat for pauses.
