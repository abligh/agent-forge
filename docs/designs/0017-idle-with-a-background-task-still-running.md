# 0017: Idle with a background task still running

* Status: approved (Alex, 2026-10-16, in XMPPEngineer's session, recorded at
  https://github.com/abligh/agent-forge/issues/26#issuecomment-6226988236)
* Implemented: v0.4.69, off until configured. Needs the agents and
  their relay on an xmpp-mcp that reports `shell` (its steps 1 and 2).
* Author: XMPPEngineer
* Date: 2026-10-16

## Summary

The router's idle offer (rules 15 and 16, `router.idle_offer`) offers
work only to an agent whose directory entry says `idle`. Claude Code
says `shell`, not `idle`, when a session's turn has ended while a
background task it started is still running. xmpp-mcp publishes no
status for `shell`, the relay reports it as `other`, and the router
reads `other` as not idle. So one background task that never ends hides
an agent from the offer for as long as it runs.

On one host on one day:

* Six developer agents spent 76 periods of 15 minutes or more in
  `other`, 53 hours in all.
* One agent was never `idle` all day, although its turns ended 34
  times.
* One had been in `shell` for 12.5 hours, owning 8 issues.
* An agent stopped with a new, unblocked issue of its own and sat until
  a person noticed. The cause was two wait loops (`until grep -q ...;
  do sleep 15; done`) on a task that had ended 47 hours earlier without
  writing the line they waited for.

This design counts an agent in `shell` for long enough as idle for the
offer, and tells it about the task that is still running.

## Design

**1. The agent's server says `shell`.** xmpp-mcp publishes a session
in `shell` with presence status `shell`, not with none. Like `idle`, it
carries a XEP-0319 `since`: when the turn ended, i.e. the session
file's write time when the status left `busy`. A change from `shell` to
`idle` keeps that time, because the turn ended only once.

**2. The relay passes it through.** `GET /directory` gives `status`
`shell` (it gives `busy`, `idle` or `other` today) and the same
`idle_since`. A router without this design reads `shell` as it reads
`other`, so nothing changes until step 3.

**3. The router counts it after `shell_minutes`.** With
`idle_offer.shell_minutes` set, an agent counts as idle for rules 15
and 16 when every directory entry of its session name is either
`idle` for `idle_minutes` or `shell` for `shell_minutes`. The idle
period starts at the entry's `since`, so a change from `shell` to
`idle` stays one period: one offer, as now. Without the key, `shell`
is not idle, as now.

**4. The offer says what is still running.** An offer to an agent in
`shell` adds one line: "A background task of yours has been running
since 11:17Z. Stop it if it is stale." The agent can see its own tasks,
and a stuck loop is usually easy to recognise once someone points at
it.

| Key | Default | What it is |
| --- | --- | --- |
| `shell_minutes` | none (off) | How long an agent's entry must have been `shell` (its turn ended, a background task still running) before it counts as idle for the offer. Longer than `idle_minutes`, because a background build is often something the agent will act on when it finishes. 20 is a reasonable start. |

## Trade-offs

* **An offer can arrive during a real wait.** In `shell` the agent
  may be waiting on a long build that it will act on when the build
  ends, which wakes the session. An offer after `shell_minutes` costs
  one turn: the agent starts the offered issue alongside, or says
  what it is waiting on. Before this design, the cost of the same
  mistake was hours of an agent doing nothing.
* **`shell` is undocumented.** It is what Claude Code 2.1.299 to
  2.1.301 writes to its session file. If it changes, the status falls
  back to none and the relay to `other`, which is today's behavior.
* **Presence history gains a value.** `forge-stats` reads the router's
  presence log, where `shell` was `other`. It counts `shell` as
  neither busy nor idle, as it counted `other`.

## Non-goals

* Stopping an agent's background tasks. They are the agent's, and the
  offer's extra line asks it to look.
* Changing Claude Code's statuses.

## Implementation plan

1. xmpp-mcp: publish `shell` with its `since` (step 1) and pass it
   through the relay's directory (step 2), with tests. A release that
   changes nothing for a router without step 3.
2. agent-forge: `idle_offer.shell_minutes`, rules 15 and 16 and the
   offer's line, in `docs/config.md` and `rules_default.py`, with
   tests. Off by default.
3. A project turns it on with `"shell_minutes": 20` in its
   `router.idle_offer`, after the hosts' agents run an xmpp-mcp with
   step 1 and their relay one with step 2.
