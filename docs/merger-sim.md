# The Merger simulation

`tests/merger_sim.py` runs the real `forge-merger` tick, live, minute
after simulated minute, and `tests/test_merger_sim.py` holds it to the
rules a merge queue must keep. The unit tests in
`tests/test_forge_merger.py` test each function against its own
design. The simulation tests what happens across ticks, where the
Merger's writes, GitHub's answers and the build check's verdicts meet.

## What it runs against

- **Real git.** A bare `origin` and the Merger's own checkout, so every
  tree, merge and conflict is git's.
- **`SimGitHub`.** It answers what the Merger reads and does what it
  writes, the way GitHub does where it matters:
  - `update-branch` makes a new merge commit, with the tree the Merger
    predicted but a new sha;
  - a squash merge lands one commit on `main`;
  - a merged branch's stacked pull requests are retargeted to `main`
    (`World.auto_retarget`);
  - statuses are kept per commit.
- **`SimCheck`.** A model of `forge-pr-check`:
  - it runs the Merger's batch tops first, then the heads no batch
    covers, in `slots` slots, each taking `minutes`;
  - it matches a head to a green run of its tree, covers a batch
    member once its top is green, and holds one while the top runs;
  - it notices a new head `notice` minutes after it appears;
  - it runs the gated instance (`vm/mac`, for paths under `mac/`) in a
    queue of its own, `mac_slots` slots taking `mac_minutes`;
  - a tree with a `red-*` file is red.

  With `later_first` set, it starts runs in `forge-pr-check`'s own
  order instead of every top first:
  1. the default branch's runs;
  2. a head that has waited `max_wait` minutes;
  3. the head of the pull request the Merger waits on;
  4. the batch's top, then the first `later_first` later tops;
  5. the other heads, oldest first;
  6. the remaining later tops, only in a slot no head wants.

  With `stop_spent`, a run of a top the Merger no longer predicts, or
  of a head no pull request has any more, stops. `background` is
  other work in the same slots, queued at its minute.

## The rules

`Monitor` checks these after every tick. A break is a line in
`Result.broken`, with the minute and what the Merger last did.

1. A pull request leaves a started train only by merging at the tree
   predicted for its place. The exceptions are a red top (the search
   for the culprit), and the scenario having pushed to or approved it
   or one ahead of it. Never through the Merger's own doing.
2. `main` only takes a tree a green run tested: its own, or a prefix
   of a train whose top was green.
3. Nothing boards a train without an approval of its author's latest
   change, nor without the gated instance's green on that change when
   it needs one.
4. Nothing red reaches `main`.

An urgent pull request goes first by design, and the trains after it
are cut again. A started train spent that way isn't a break of rule
1: it is listed in `Result.recuts`, so its cost can be seen.

`Result` also carries the figures: merges, simulated hours, build
runs, batch-top runs, and the Merger's own output lines.

## Adding a scenario

A scenario is a list of `(minute, World method, args)`:

- `open(number, base=None, mac=False, red=False, urgent=False)` opens
  a pull request, stacked on `base` when given, and approves it;
- `push(number)` is its author pushing a change of their own;
- `review(number)` approves its head again.

`day()` is the short scenario the normal suite runs. `busy()` builds a
longer one from a seeded random source, and
`AGENT_FORGE_SIM_LONG=1` runs it. Run an older Merger by passing
`load_merger(merger_source("<tag>"))` to `Sim`.

## Replaying a real day

A real day's logs can be replayed, to see what a setting would have
done before it goes live. It takes two steps.

**1. Take a trace** from the check host's logs:

    python3 -m tests.merger_trace --merger <merger state>/merger.jsonl \
        --verdicts <pr-check state>/verdicts.log \
        --since 2026-09-01T00:00:00Z --until 2026-09-02T00:00:00Z \
        --slots 7 --urgent 123,456 > trace.json

The trace is per pull request: when it opened and was approved, its
pushes, its base when stacked, whether it waited for the gated
instance, whether its first head was red and when it was fixed, the
pull requests it conflicted with, its run length, its hand-backs, and
when it really merged. It also carries the day's other work in the
build check's slots (the default branch's runs, and runs of pull
requests never approved), and the day's real figures. What the logs
can't say is inferred and listed in its `inferred`. For example, a
conflict with the default branch is put down to the pull request
merged last before it. A real project's trace is that project's
history: keep it out of any repository.

**2. Replay it**, with the project's `merge` section (and `checks`'
`slots` and `max_wait_minutes`) as JSON:

    python3 -m tests.merger_replay trace.json --config merge.json \
        --compare test_ahead.later_first=2

The pull requests arrive, are approved, pushed to, fixed and handed
back when the trace says. Everything else is the simulation's own:
the real Merger's ticks, the build check's model in the trace's slots
with the day's other work, and authors answering a conflict with the
default branch the trace's typical time after it appears. `--set K=V`
changes a setting under `merge` (`checks.slots=8` for the slots), and
`--compare` replays the day again with more changes. It prints these
beside what really happened:

- merges, an hour and in each hour;
- build runs, and how many were train tops;
- runs a merge, and the median time from approval to merge;
- how busy the slots were, and runs stopped as spent;
- started trains re-cut, and rule breaks.

Settings the simulated GitHub can't serve (`code_owners`, the `gate`
tools) are left out and said so. `--json` gives everything as JSON.

Reality and the replay differ in what the replay can't know, so
compare settings with each other first, and with reality second.
The replay can't know:
- a host's faults, and the Merger's own outages;
- the reviewers' pace past their first approval;
- a train going red without one red pull request in it.

A quiet minute, one in which nothing the Merger reads has changed
since a tick that did nothing, is skipped, at most `--idle` (10) in a
row. The result is the same, which a test checks. Most of the time
left is the Merger's own git calls, about two hundred a tick with two
hundred pull requests, so a busy day (about 200 pull
requests, 1,440 minutes) takes six to eight minutes on one core.
