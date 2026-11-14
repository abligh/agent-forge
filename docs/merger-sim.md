# The Merger simulation

`tests/merger_sim.py` runs the real `forge-merger` tick, live, minute
after simulated minute, and `tests/test_merger_sim.py` holds it to the
rules a merge queue must keep. The unit tests in
`tests/test_forge_merger.py` test each function against its own
design. The simulation tests what happens across ticks, where the
Merger's writes, GitHub's answers and the build check's verdicts meet.

## What it runs against

- **Real git.** A bare `origin` and the Merger's own checkout, so every
  tree, merge and conflict is git's. Every push the Merger makes to the
  project's GitHub remote goes to `origin` instead. Any other network
  address given to a git command that reaches a remote fails the run
  (`NetworkUsed`): nothing a simulation runs leaves the host. The
  Merger commits as `IDENTITY` when its own `RESOLVE_IDENTITY` is unset,
  so train commits and resolutions are made as they are in production.
- **`SimGitHub`.** It answers what the Merger reads and does what it
  writes, the way GitHub does where it matters:
  - `update-branch` makes a new merge commit, with the tree the Merger
    predicted but a new sha;
  - a squash merge lands one commit on `main`;
  - with `train_merge`, the train's pull request is opened from the
    branch the Merger pushed and merged as a merge commit. Every open
    pull request whose head that puts in `main` is marked merged,
    bottom first, at the tree of the train commit that took it in
    (`SimGitHub.trains`);
  - a merged branch's stacked pull requests are retargeted to `main`
    (`World.auto_retarget`);
  - statuses are kept per commit.
- **`SimCheck`.** A model of `forge-pr-check`:
  - it runs the Merger's batch tops first, then the heads no batch
    covers, in `slots` slots, each taking `minutes` (a stack's layer's
    own run takes `layer_minutes` when given, to show what waits for
    those, and any other pull request's own run `own_minutes`, to show
    what waits for own runs queued behind many others);
  - it matches a head to a green run of its tree, covers a batch
    member once its top is green, and holds one while the top runs;
  - it goes by tree for the Merger's tops too, as `next_ahead` and
    `merger_spent` do: a top whose tree has a verdict or is running
    gets no run of its own. A re-cut that makes the same prediction
    under a new commit keeps the run it has, and isn't stopped as spent;
  - it notices a new head `notice` minutes after it appears;
  - it runs the gated instance (`vm/mac`, for paths under `mac/`) in a
    queue of its own, `mac_slots` slots taking `mac_minutes`;
  - a tree with a `red-*` file is red, and so is one with two
    `clash-<name>-*` files of the same name (two changes each green
    alone, red together).

  With `later_first` set, it starts runs in `forge-pr-check`'s own
  order instead of every top first:
  1. the default branch's runs;
  2. a head that has waited `max_wait` minutes;
  3. the head of the pull request the Merger waits on;
  4. the batch's top, then the first `later_first` later tops;
  5. the other heads, oldest first;
  6. the remaining later tops, only in a slot no head wants.

  With `train_slots` too (read from `merge.test_ahead` in a replay),
  up to that many slots are kept for the Merger's predictions, only as
  many as are running or waiting to start; heads and the background
  fill the rest.

  With `stop_spent`, a run of a top the Merger no longer predicts, or
  of a head no pull request has any more, stops. `background` is
  other work in the same slots, queued at its minute.

## The rules

`Monitor` checks these after every tick. A break is a line in
`Result.broken`, with the minute and what the Merger last did.

1. A pull request leaves a started train only by merging at the tree
   predicted for its place. The exceptions are a red top (the search
   for the culprit), one found red on its own that minute (boarded
   without its own green, `own_green_to_board` false), and the
   scenario having pushed to or approved it or one ahead of it. Never
   through the Merger's own doing.
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

`open` also takes `clash`, a name: two pull requests opened with the
same one are each green alone and red together.

`day()` is the short scenario the normal suite runs;
`restacked_day()` adds a push to a stack's bottom layer, and
`siblings_day()` a base with three layers side by side on it.
`red_train_day()` has a long train with two clashing members, and
`red_stack_day()` a stack of three in a train whose top layer clashes
with another member; the normal suite runs the latter with
`train_merge`. `busy()`
builds a
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
pushes, its base when stacked, whether it waited for the gated instance,
whether its first head was red and when it was fixed, the pull requests
it conflicted with, its run length, its hand-backs, and when it really
merged. With `--repo DIR` (a clone holding the heads, and `--main-ref`,
default `origin/main`), it also records each pull request's changed
lines: per file, the ranges of the merge base its last checked head
replaces. The replay writes those ranges into real files on its own
main, so two pull requests conflict where their lines meet, as git
decides, not only where the logs said they did. It also carries the
day's other work in the build check's slots (the default branch's runs,
and runs of pull requests never approved), and the day's real figures.
What the logs can't say is inferred and listed in its `inferred`. For
example, a conflict with the default branch is put down to the pull
request merged last before it. A real project's trace is that project's
history: keep it out of any repository.

**A heavier day than any real one** comes from overlaying traces:

    python3 -m tests.merger_overlay a.json b.json [--copies N] \
        [--shift MINUTES] > heavier.json

Each trace after the first, or each copy of one with `--copies`, has
its pull requests renumbered past the ones before it and its minutes
moved `--shift` later (0 by default: the days on top of each other).
Their `files` keep their paths, so where two days' pull requests
change the same lines the replay finds them conflicting, and conflicts
grow with the load. A trace taken without `--repo` adds no conflicts
across days, and the result's `inferred` says so.

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
- runs a merge, and the median times from opening to approval and
  from approval to merge;
- how busy the slots were, and runs stopped as spent;
- train tops found red, started trains re-cut, pull requests that
  left a started train and never merged at its tree, and rule breaks.

Settings the simulated GitHub can't serve (`code_owners`, the `gate`
tools) are left out and said so. `--json` gives everything as JSON.

The trace's approval times include the real wait for the real build
check, so by default the real queue is part of the input, and a change
to the check can't show in the wait for approval. Two options take
that clock away from the trace:

- `--approve-after-green MINUTES`: each pull request the trace
  approved is approved that long after the simulated check goes green
  on its head, and again after a new head loses the approval. A pull
  request the trace never approved stays unapproved.
- `--fix-after-red MINUTES`: a red pull request's author pushes the
  fix that long after the simulated check says red, not at the
  trace's time.

Reality and the replay differ in what the replay can't know, so
compare settings with each other first, and with reality second.
The replay can't know:
- a host's faults, and the Merger's own outages;
- the reviewers' pace past their first approval (with
  `--approve-after-green`, a fixed delay stands in for it);
- a train going red without one red pull request in it.

A quiet minute, one in which nothing the Merger reads has changed
since a tick that did nothing, is skipped, at most `--idle` (10) in a
row. The result is the same, which a test checks. Most of the time
left is the Merger's own git calls, about two hundred a tick with two
hundred pull requests, so a busy day (about 200 pull
requests, 1,440 minutes) takes six to eight minutes on one core.

## Comparing two versions on the library's days

A change to which pull request the Merger takes next, or to what the
build check runs first, is judged on the same days as every other such
change (the rule is in CLAUDE.md):

    make replay-compare BEFORE=origin/main AFTER=.
    python3 -m tests.merger_compare --before REF --after REF \
        [--day KIND|TRACE ...] [--config FILE] [--set K=V ...] \
        [--approve-after-green MINUTES] [--fix-after-red MINUTES]

Each version is checked out in a temporary git worktree (`.` is the
working tree as it is) and replays each day with the same settings and
the same `PYTHONHASHSEED`. A replay is the same every time it runs: the
simulation dates every commit with one fixed date, not the clock, so no
SHA, and no order that follows one, changes from run to run (#413). A
version from before that may vary a little between runs. The output is a
Markdown table to paste into the pull request: merges, approval to merge
(median and p90), hand-backs, build runs a merge, red train tops and
rule breaks, before and after, for each day. A figure an older version
doesn't report shows as `-`.

The days are the library's, `tests/days/*.json`, made by
`tests/merger_days.py` from a seed and a few figures, so they hold no
project's pull requests, names or times:

- `ordinary`: a working day, about 120 pull requests on 5 slots;
- `heavy`: twice the arrivals on the same slots, so the queue fills;
- `bad`: an ordinary day with three times the reds, the conflicts and
  the pushes after approval;
- `smoke`: three hours and a dozen pull requests, small enough for the
  test suite, which replays it.

They have no reality to compare with: the table compares the versions.
A project's own recorded day (`tests/merger_trace.py`) can be passed
as `--day FILE` too, kept where the project keeps its own data.
