# 0061: Ask whether a Blocked-by is stale

* Status: approved (Alex Bligh, 2026-11-19, #495)
* Author: XMPPEngineer
* Date: 2026-11-19
* Issue: #493

## Summary

A `Blocked-by: #N` line holds its item, in the Router and in
forge-merger, until #N closes. When #N is an issue that stays open
after its code has merged, the line goes on holding work that only
needed the code. This design adds a Router rule, rule 26: when an
item's `Blocked-by:` names an open issue and a pull request that
references that issue has merged since the line was written, the
item's owner is asked once whether the line is stale. The setting is
`router.stale_blocker`, off by default. The idea is the owner's.

## The evidence

On one project's host on 2026-11-19 at about 08:20Z:

* 40 of 76 open pull requests were approved, and the Merger held 31 of
  them. 14 were held, directly or through the stack below them, by
  `Blocked-by:` lines on four step issues.
* Each of the four step issues had a merged pull request that
  implemented it: three merged between 16:28Z and 18:14Z the day
  before, and one at 07:45Z that morning. The issues stayed open, by
  that project's rule, until the step passed its checks on other
  platforms (a nightly run, a turn on a particular machine).
* The pull requests held needed only the step's code in the default
  branch. Once their owners removed the lines, they could board trains.
  Nothing had asked them to: rule 14 speaks only once every item a line
  names has closed.

## Why it asks, rather than clearing the line itself

Sometimes the open issue is the real dependency. A step may wait for
the earlier step's checks to pass on another platform before building
on it. Only the item's owner knows which kind of wait the line records,
so the rule asks and changes nothing. forge-merger's reading of
`Blocked-by:` is unchanged.

## What changes

1. **The lookup.** For each open issue that an open item's `Blocked-by:`
   lines name, the Router also asks GitHub for the pull requests that
   reference it: the issue's timeline events `CROSS_REFERENCED_EVENT`
   and `CONNECTED_EVENT` whose source is a pull request, with its state
   and `mergedAt`. This is batched as `attach_blockers` batches its
   query, and only when `router.stale_blocker` is on. The result goes
   into the item's `blockedBy` entry for that issue as `mergedRefs`:
   `[{number, mergedAt}]`, merged ones only.
2. **The rule (26).** It applies to an open issue or pull request when
   one of its `Blocked-by:` lines names an issue that is open, and a
   pull request in that issue's `mergedRefs`:
   * merged after the line was written: after the body's last edit or
     creation, or after the comment that set the lines
     (`issue_blocker_state`'s `written`);
   * and is not the item itself.

   It is then the item owner's turn (`Owner:<Name>`, else the author),
   with the event the newest such `mergedAt`. The text reads: "says
   Blocked-by: #N, which is open, but #P, which references it, merged
   at T. If #N's code in the default branch is all this waits for,
   remove the line or write what it waits for now. If it really waits
   for #N itself to close, leave it."
3. **How often.** The Router's event key is the newest merge, so each
   new referencing merge asks once, and nothing reminds. An owner who
   leaves the line hears nothing more until another pull request for
   #N merges.
4. **Where it sits.** In `route_issue`, after rule 14 and before rule
   24. In `route_pr`, where a pull request's rules have no other turn
   for its owner. It never takes a turn from a rule above it.

## The cost

One more GraphQL query per Router run while any open item names an open
issue in `Blocked-by:`: one field per issue named, at most 50 events
each. A cross-reference is a mention, so a pull request that only
mentions #N in passing also triggers the ask. That is one question,
answered by leaving the line. Requiring a closing reference instead
would miss the common case: a step's pull request that doesn't close
its issue, because the issue waits for other checks.

## Not done

* No marker that silences the ask for one line. Asking once per new
  referencing merge keeps it rare enough without one.
* forge-merger's holds are unchanged. It goes on reading the line as
  written.
