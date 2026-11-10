# 0048: Nightly steps run on the pull requests that can break them

* Status: approved (Alex, 2026-11-05)
* Author: XMPPEngineer
* Date: 2026-11-05

## Summary

A project's nightly (designs/0028) runs steps the build check never
runs: slow tests, corpus and threshold tests, round trips through
other programs, other platforms. A pull request that breaks one of
them passes its check, merges, and is found only that night. Fixing it
then takes a rerun cycle of hours, and the next break is found the
same way.

This design moves that finding to before the merge, for the steps
where it can be done, and makes the remaining red nights shorter and
quieter. Each part is off until a project sets it:

1. **A nightly step can also be a check step, scoped by paths.** A
   step in `checks.steps` with `pull_requests: {paths, label}` runs on
   a pull request's head only when the head changes a matching path or
   carries the label. It runs in the same check, under the same
   context, so the Merger already gates on it.
2. **Scoped steps run last and have their own budget**, so a cheap
   head stays fast and a slow step can't hold a slot for ever.
3. **Batches and trains** run a scoped step when any member would.
   The default branch runs it when its changes since the last checked
   head match.
4. **The nightly reruns when the fix merges**, read from what the fix
   names, not only from a closing keyword for the nightly's issue.
5. **The nightly issue says when the next answer is due**, with a
   `Defer-until:` line, so the router stops offering it to agents
   while a rerun is in progress.

## Evidence

One project, 25 October to 5 November.

* **21 of 26 dispatched nightlies were red.** The nightly was red
  without a break from 3 November 07:14Z to the time of writing, about
  46 hours, through five fix-and-rerun cycles.
* **The red steps were almost all ones the build check never runs.**
  Counting each failed step once a run:
  - a slow-test step, 6;
  - a test against an outside service, 5;
  - a corpus step, 4;
  - two threshold steps that compare against another program's
    renders, 2 each;
  - an advisory check, 1;
  - a second platform's test or build, 4; a third platform's, 3;
  - a release upload, 2.

  The retry's "steps red last time, first" step failed 5 more times,
  repeating one of these.
* **The breaks came from pull requests that passed every check.** In
  one 24-hour chain: a pull request added a corpus pass with
  thresholds for part of the corpus; two others changed rendering.
  Each merged green and broke a threshold step that night. One more
  red was the threshold step failing because 35 entries had
  *improved*.
* **The rerun after a fix waited for the timer.** A fix merged at
  22:25Z. Its pull request closed its own issue, not the nightly's,
  so §3 of designs/0028 didn't fire, and the rerun came at 02:15Z,
  four hours after the last run ended. Runs take one to six hours, so
  each cycle cost four to ten.
* **The issue drew comments, not fixes.** The nightly's issue at the
  time had 27 comments. About 6 found a cause or named a fix. Most of
  the rest were agents saying there was nothing to do until the rerun
  finished, several of them because the router had offered the issue
  to them as unattended. Twice, two or three agents claimed the same
  red within a minute of each other.

## The design

### 1. A scoped step: `pull_requests` on a step

A step in `checks.steps` gains `pull_requests`, the same shape as an
instance's (designs/0034): `{"paths": [glob, ...], "label": name}`,
either left out but not both.

* **On a pull request's head**, the step runs only if the head's
  changes since its merge base touch a path matching `paths`, or the
  pull request carries `label`. Otherwise it is left out of the run,
  as a step `depends` doesn't choose is. The verdict lists it as
  `skipped: out of scope`.
* **With `depends`**, the scope decides, not the tracer. A slow step
  is traced rarely and its record goes stale; `paths` is the
  project's own judgement of what can break it. `reuse` still applies:
  a green verdict on the same inputs stands.
* **The step is the nightly's step.** The project makes the check
  run the same command its nightly runs. Nothing here reads the
  nightly's workflow; the nightly stays the full run and the backstop
  for whatever `paths` misses.

The project chooses which nightly steps to scope. A step that needs
another platform stays on that platform's instance (designs/0034),
whose `pull_requests` already scopes it. A step that needs an outside
service or a release upload stays nightly-only.

### 2. Last, and on a budget

* **Scoped steps run after every unscoped step**, and only if those
  are green. A head that fails a fast step reports in minutes, as
  today. With `side_by_side` (designs/0029), a scoped step is placed
  after the others unless its `after` says otherwise.
* **`scoped_minutes`** (default 60): the most a head's scoped steps
  may run, together. When it runs out, the steps still running are
  stopped and the verdict is red for that step with the reason
  `over the scoped budget`. The owner then narrows `paths` or splits
  the step; the budget keeps one slow head from holding a slot for
  the morning.
* **Each scoped step keeps `step_timeout_minutes`.** A project with
  hour-long steps raises it for the check, as today.
* **`fast_lane`** (designs/0006) never takes a head with a scoped step
  in its run.

### 3. Batches, trains and the default branch

* **A batch's or a train's top** (designs/0032, 0047) runs a scoped
  step when any member's changes match its `paths` or any member
  carries `label`. Its verdict is every member's, as now.
* **A default-branch head** checked under `main: every` or `newest`
  runs a scoped step when its changes since the last default-branch
  head checked here match. Under `needed` it doesn't: the nightly is
  the default branch's full run.
* **A red scoped step on the default branch** is a red default branch
  for `main_red_issue` (designs/0011), like any other step. A break
  that `paths` missed is then reported within one check of its merge,
  not that night.

### 4. The nightly reruns when the fix merges

designs/0028 §3 reruns the nightly early when a pull request that
closes the nightly's issue merges. Fixes rarely say that. A fix closes
its own issue, or names the nightly's issue without a closing keyword.

The rule becomes: a pull request **fixes** the nightly's issue when it
merged after the newest run started and:
- it closes the nightly's issue (as now); or
- it names the issue by number in its title, body or commits (`#N`,
  any keyword or none); or
- it closes an issue that the nightly's issue names, in its body or a
  comment, or that names the nightly's issue.

The tick dispatches when such a pull request has merged and no run is
queued or in progress, as now. Each dispatch comment names the pull
request and which of the three it matched. The same set of pull
requests goes first while the issue is open (designs/0028 §2).

### 5. The issue says when the next answer is due

Each dispatch comment ends with a `Defer-until:` line, at the expected
end of the run: its start, plus the median length of the last five
green-or-red runs of the job, rounded up to the next quarter hour. A
`first` retry (designs/0028 §4) that fails fast simply ends early; the
next red comment supersedes the line.

The router already holds an issue whose newest `Defer-until:` is still
to come (designs/0004). So, while a rerun is in progress, the issue is
not offered as unattended, and nobody is woken to say there is
nothing to do. When the run ends red, forge-nightly's red comment
names the owners and carries no `Defer-until:`, and the router routes
the issue again at once.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `checks.steps[].pull_requests` | none | `{"paths": [glob, ...], "label": name}`: a scoped step (§1). Without it a step runs as today. Refused in an instance's steps, whose block-level `pull_requests` already scopes the whole instance. |
| `checks.scoped_minutes` | `60` | The most a head's scoped steps may run together (§2). At least 1. |
| `nightly.fixes_named` | `false` | A pull request that names the nightly's issue, or closes an issue it names, counts as a fix (§4). |
| `nightly.defer_until` | `false` | Dispatch comments end with a `Defer-until:` line at the run's expected end (§5). |

Validation refuses a `pull_requests` with neither `paths` nor
`label`, an empty glob, a `label` that isn't a string, and
`scoped_minutes` below 1. `fast_lane` with a scoped step is allowed;
the lane just never takes those heads.

## Measuring it

Before a project turns §1 on, and a week after:
- red nightlies a week, and the steps that failed;
- for each red, whether a scoped step would have run on the pull
  request that broke it (its changes against `paths`);
- the check's median and 90th-percentile run time, all heads and
  heads that ran a scoped step;
- for §4 and §5, the time from a fix's merge to the next run's start,
  and the number of comments on a nightly issue that name no cause or
  fix.

`forge-stats` gains `scoped_seconds` (the scoped steps' share of each
run) so `step_alert` and the flow page show what they cost.

## Order of work

1. `forge-pr-check`: `pull_requests` on a step, the skip and the
   verdict's `skipped: out of scope`; `scoped_minutes`; ordering after
   unscoped steps, with and without `side_by_side`; tests.
2. `forge-pr-check`: batches, trains and default-branch heads (§3);
   tests.
3. `forge-nightly`: `fixes_named` (§4) and `defer_until` (§5); tests.
4. `agent_forge/config.py` validation, `docs/config.md`, and a
   release.

Parts 3 and 4 don't depend on 1 and 2, and can ship first.

A project then adds the nightly's steps it wants scoped to its
`checks.steps`, with `paths`, and turns on the two nightly keys.

## Related, not in this design

* **The Merger and instance contexts.** Today the Merger requires
  only `checks.context`. An instance's context, even when its
  `pull_requests` scope matches the head, gates nothing, so a pull
  request whose platform run is still pending can merge. A separate
  fix makes the Merger wait while an applicable instance context is
  pending or red. It doesn't depend on this design: §1 puts scoped
  steps in the context the Merger already requires.
* **Threshold tests that fail on improvement** are a project's own
  tests. The project can make them pass when a result gets better and
  lower the threshold in a separate, routine step. That removes reds
  that no rerun cycle can avoid.

## What it costs

* **Longer checks for the pull requests that touch scoped paths.**
  Those heads now wait for steps that took minutes to an hour in the
  nightly. That is the point: the author hears before merging. Other
  heads are unchanged.
* **More load on the check's hosts.** Bounded by `scoped_minutes` a
  head, and by how narrow `paths` are. `reuse` saves repeats on the
  same inputs.
* **`paths` are kept by hand and can miss.** The nightly and §3's
  default-branch runs remain the backstop.
* **A `Defer-until:` can be early or late.** Early: the router offers
  the issue for a few minutes before the run ends, as now. Late: a
  run that ends sooner is reported by the red comment, which routes
  it at once.

## Amendment, 2026-11-10: a step's secrets

A nightly step that needs secrets, such as a test account's tokens for
a third-party service, gets them from the CI system's own secrets. The
build check runs outside CI and can't see them, so such a step either
skips on pull requests (green, but nothing tested) or stays
nightly-only. A step's `env_file` closes the gap. It names a file on
the check host of `KEY=VALUE` lines, added to that step's environment
only.

* The file must be the check user's, at mode 0600. Otherwise the step
  is red without running, and its tail says why without naming a value.
* A missing file is logged, and the step runs without it, so a host
  without the secrets still checks everything else.
* No value is written to a log, comment, state or digest.
* Any head that runs the step can read the values. The check runs only
  branches of the project's own repository, and `pull_requests` scopes
  the step to the code that uses them.
