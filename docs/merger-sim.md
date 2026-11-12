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
    covers, in `slots` slots, each taking `minutes` (a stack's layer's
    own run takes `layer_minutes` when given, to show what waits for
    those);
  - it matches a head to a green run of its tree, covers a batch
    member once its top is green, and holds one while the top runs;
  - it notices a new head `notice` minutes after it appears;
  - it runs the gated instance (`vm/mac`, for paths under `mac/`) in a
    queue of its own, `mac_slots` slots taking `mac_minutes`;
  - a tree with a `red-*` file is red.

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

`day()` is the short scenario the normal suite runs;
`restacked_day()` adds a push to a stack's bottom layer, and
`siblings_day()` a base with three layers side by side on it. `busy()`
builds a
longer one from a seeded random source, and
`AGENT_FORGE_SIM_LONG=1` runs it. Run an older Merger by passing
`load_merger(merger_source("<tag>"))` to `Sim`.
