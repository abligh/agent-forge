# 0001: Fewer wasted rounds in the merge queue

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-10

## Summary

On one day, one project's Merger handed back 61 heads of 34 pull
requests, 32 of them because the head conflicted with `main`. Three
patterns account for most of the waste:

* **One author's overlapping pull requests churn.** Three pull requests
  by one agent all changed the same source file and a generated digest
  file. Over 35 minutes, each merge left the others conflicting. Each
  was handed back, rebased, regenerated, approved again and rebuilt, and
  the next merge undid it.
* **A pull request that fails at the front blocks the queue.** The
  queue is oldest-opened first, so a handed-back pull request goes back
  to the front as soon as it is approved again. One was handed back
  three times in 20 minutes, each time from the front, and every
  prediction behind it (`merge.test_ahead`) was wasted.
* **An author doesn't learn that her pull request merged.** No rule
  fires on a merge. One agent waited on her own pull request, which
  merged, and sat idle for 45 minutes until someone told her.

This proposes three rules, each off until a project turns it on:
`merge.hold_conflicting`, `merge.yield_after_hand_back` and
`router.merge_notice`.

## Goals

* **One rebase per merge**, not one per overlapping pull request.
* **A pull request that keeps failing lets others through**, without
  losing its place for good.
* **An author hears that her pull request merged**, with what it
  unblocks and what to do next.
* **No rule about what merges changes.** Approvals, the build check,
  the gate and the coverage of an approval stay as they are.

## Non-goals

* **Per-agent long-lived branches**, which would remove the first
  pattern differently. They are to be measured first.
* **Resolving conflicts automatically.** A conflict is still resolved
  by its author, and the resolution approved again.
* **Changing who is handed what.**

## Design

### Hold a pull request that conflicts with one ahead of it

`merge.hold_conflicting: true` (`forge-merger`). Each tick, every pull
request in the queue except the first is checked against those ahead of
it, with `git merge-tree --write-tree` of the two heads. One whose head
conflicts with a head ahead of it (Q) is **held**, not handed back. It
keeps its approval, and it isn't updated, merged or predicted. Its
`held` reason reads `conflicts with #Q, ahead of it in the queue`. The
Merger says so on it once per head and Q, asking the author to leave it
until Q merges. When Q has merged, it is judged as usual, and handed
back once if it conflicts with `main`. One rebase covers both.

The comment is not a hand-back, so the router wakes nobody for it.

### Yield after a hand-back

`merge.yield_after_hand_back: N` (`forge-merger`). A pull request
handed back to its author in the last 24 hours lets the next N
candidates go ahead of it for each such hand-back, counting at most
`merge.yield_cap` hand-backs (default 3). Only
hand-backs to the author count. One to the approving reviewer asks for
a fresh review (for example of a conflict's resolution) and is not the
pull request's failure. A pull request another is blocked by keeps its
precedence. The one the Merger is waiting on keeps its place, so its
prediction isn't thrown away.

### Tell an author her pull request merged

`router.merge_notice: {"window_minutes": 30}` (`forge-router`'s default
rules, rule 17). Each run reads the pull requests merged in the window
and tells each agent author once:

    #170 "the adapter interface" merged at 14:53Z. It unblocks #171
    (UIDev). Your oldest item nothing blocks: #137 "the demonstration".

It is about no open item and keyed on the pull request and its merge
time, so it is said once and never reminded. A search that fails says
nothing: "unknown" is never "nothing merged".

## Config and file-format shape

In a project's `.agent-forge/config.json`, beside the existing keys:

```json
"merge": {"hold_conflicting": true, "yield_after_hand_back": 2,
          "yield_cap": 3},
"router": {"merge_notice": {"window_minutes": 30}}
```

`docs/config.md` defines each. The Merger records holds under `holds`,
and yields under `yielded`, in each tick's record. `held-conflicting.json`
in its state directory remembers what it said.

## Trade-offs and failure modes

* **A held pull request can wait longer than it would have.** If Q is
  handed back, the held one waits for Q's fix. It is checked every
  tick, so once Q leaves the queue, or Q's new head no longer conflicts
  with it, it is free.
* **The hold costs git work each tick**: one `merge-tree` per pair, at
  most 45 for a queue of ten, each well under a second.
* **Yielding delays a pull request handed back for a flake**: a few
  places, capped.
* **One more message per merge**, read at the author's next turn. It
  repeats what rule 14 tells the owners of unblocked items, which rule
  14 still does.

## Testing strategy

* **agent-forge's tests** for each rule.
* **In a project, a dry run of the Merger** with both merge keys,
  beside the live one, for an hour, compared by hand: the holds and
  yields it would make.
* **Afterwards, a day's numbers**: hand-backs per merge, conflict
  hand-backs, and repeat hand-backs of one pull request.

## Implementation plan

1. The three rules, in one release (v0.4.4).
2. Each project turns on what it wants, in its own configuration.

## Open questions

* **The yield.** Default: N = 2, with at most three hand-backs counted.
* **The merge notice's window.** Default: 30 minutes, which covers a
  router outage of that long.
