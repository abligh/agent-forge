# 0031: The next merge's run gets the CPU

* Status: approved (Alex Bligh, 2026-10-25, https://github.com/abligh/agent-forge/issues/72#issuecomment-6399698624)
* Author: XMPPEngineer
* Date: 2026-10-25

## Summary

The Merger merges one pull request at a time, and each merge waits for
one `forge-pr-check` run: of the pull request at the front of its queue,
or of the prediction for it (`merge.test_ahead`). `merger_first` and
`test_ahead` already put that run first in the queue, so it never waits
for a slot. Once it has a slot, though, it shares the CPU equally with
every other run, and on a busy host that roughly doubles how long it
takes. So the time between merges doubles too.

This design gives that one run, the run the next merge waits on, a
higher CPU weight than every other run. Other runs keep `cpu_weight`.
A step that starts containers can also put them in a slice that has the
same priority. Both settings are off until set.

## Evidence

On one host with 32 CPUs and 6 slots, `merge.test_ahead` at depth 4,
and `merger_first` on, over three hours of an afternoon's burst:

| Runs | Count | Mean wait for a slot | Mean run |
| --- | --- | --- | --- |
| The Merger's predictions | 31 | 0 s | 924 s |
| Every other pull request's head | 63 | 932 s | 527 s |

* **The Merger never waited.** Queue order isn't the problem.
* **Its runs were slow.** Predictions run every step a head needs on
  the merged tree. Earlier that day, before the burst, a run's median on
  the same host was 478 s. In the afternoon the load average was 71 to
  102 on 32 CPUs, and the CPU pressure stall (`some`) was about 33%.
* **Each merge cost a run.** Of the 31 predictions, 20 were used by a
  merge (`MATCHED`). The time between merges followed the predictions'
  run time: 10 to 24 minutes per merge.
  The approved queue grew from 11 to 15 pull requests.
* **The steps already weigh the same.** Every step's scope has
  `CPUWeight` `cpu_weight` (1000), so the Merger's run and five others
  get one sixth each.
* **Containers escape the weight.** A step that runs a browser in a
  container puts its CPU in the container daemon's cgroup, not in the
  step's scope. That host's daemon puts containers in a slice of weight
  25. The browser processes there used about 12 of the 32 CPUs, whose
  run they belonged to regardless.

## Design

### 1. `checks.merger_cpu_weight`

`merger_cpu_weight` (none by default: off) is the `CPUWeight` of every
step's scope in **the run the next merge waits on**. That is either:

* the run of a head of the pull request in the Merger's `next.json`,
  while it is fresh (as `merger_first` reads it); or
* with `merge.test_ahead`, the run of the first prediction in
  `ahead.json` (k = 0), the one the Merger acts on next.

Every other run, including the predictions behind the first, the
default branch's runs and the fast lane, keeps `cpu_weight`. The weight
is read when each step starts (with `cpu_share: "step"`, alongside
`CHECK_CPUS`). So a run that becomes the front of the queue part-way,
because the one ahead merged, gets the weight from its next step on.
A step already running keeps the weight it started with.

The run's log line and its verdict record say `merger_weight: true`.
`step_alert` compares a step only with runs that have the same value,
as it does for `side_by_side`.

It must be at least `cpu_weight` and at most 10000, systemd's limit.
A value of 4000 to 10000 is the intended range. Weights only matter
under contention: on an idle host every run still gets every CPU it
can use.

**Why only the front, and not every prediction.** With
`test_ahead.depth` 4, predictions can hold five of six slots. Weighting
them all would leave the remaining slot almost nothing, and the
predictions further back are the ones most often thrown away when the
queue reorders. The front run is the only one a merge is certain to
wait on.

### 2. `CHECK_DOCKER_SLICE`

`checks.docker_slices`, `{"front": "<name>.slice", "other":
"<name>.slice"}` (none by default), names two slices the host already
has. Each step gets `CHECK_DOCKER_SLICE`, `front` for the run the next
merge waits on and `other` for the rest. A step that starts containers
passes it to `docker run --cgroup-parent`. Nothing changes for a step
that doesn't. Creating the slices and their weights is the host's job,
as `user-docker.slice` is today. agent-forge only names them.

### What doesn't change

Queue order (`merger_first`, `first_authors`, `max_wait_minutes`,
`urgent`), slots, `cpu_share`, `side_by_side` and verdicts don't change.
So `max_wait_minutes` still bounds how long another head waits to
start. Its run may then take longer while the front run is busy. That
is the trade-off: the next merge comes sooner, and other checks get
the CPU that's left.

## Measurement

After the setting goes on, compare with the table above, over a day of
at least 20 merges:

* the front run's median wall time, which should fall towards the
  quiet-host median;
* the median time between merges while the approved queue is non-empty;
* other heads' median run and wait, so their cost is visible.

If the front run doesn't get faster, the weight is turned off again.
The figures go on the approval issue.

## Order of work

1. `merger_cpu_weight`, the verdict field and `step_alert`'s split,
   with tests.
2. `docker_slices` and `CHECK_DOCKER_SLICE`.
3. Docs: docs/config.md's `checks` table.
