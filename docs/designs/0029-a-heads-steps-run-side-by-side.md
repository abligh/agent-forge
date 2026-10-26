# 0029: A head's steps run side by side

* Status: approved (Alex Bligh, 2026-10-24, https://github.com/abligh/agent-forge/issues/59#issuecomment-6382972434)
* Author: XMPPEngineer
* Date: 2026-10-24

## Summary

`forge-pr-check` runs a head's steps one after another, so a head's
wall time is the sum of its steps. Some steps barely use a CPU (they
wait on a container or a browser) while the host has CPUs to spare.
This design makes two changes, each with its own switch:

1. **A step's CPU share is taken when the step starts**, not when the
   run starts.
2. **A run's steps run side by side**, with the steps that measure
   time run on their own at the end.

## Evidence

From one project's build-check host, 24 hours of records (16 cores, 32
threads, 6 slots plus the fast lane):

- A head that changes code runs 8 to 13 steps. Their walls add up to
  306 s md (p10 52 s, p90 506 s), and the longest step takes 119 s md.
- Three steps that use under one CPU each (an end-to-end scenario in a
  container, a browser suite and a timing) take about 100 s of a code
  head's ~300 s.
- The check used 10.8 of the 32 CPUs on average, with 5 to 9 in the
  quietest hours and 20.8 in the busiest. A head waited 5 s md for a
  slot (p90 115 s), so slots weren't the bottleneck.
- The CPU-bound test step ran with a `CHECK_CPUS` of 5 to 14. A run's
  share is fixed when it starts: 32 divided by the slots busy then,
  never under 32 / 7 = 4. A run that starts while four others are
  going keeps 6 CPUs after they finish and the host goes idle.

Estimated from the records (not measured): with the steps side by side,
a code head comes to about 170 s at today's shares, and to about
100–120 s if the CPU-bound step also gets 16 CPUs.

## Design

### 1. A step's share is taken when it starts

`checks.cpu_share: "step"` (default `"run"`, today's behaviour). With
it, each step's `CHECK_CPUS` is worked out as it starts, from the slots
busy at that moment. The rule is unchanged: the online CPUs divided by
the busy slots (counting this one), never less than the all-slots
share, and at least 1. A host that sets `CHECK_CPUS` itself still wins.

A long step that starts after the short ones then gets the CPUs that
runs which have since finished let go. A step keeps its share until it
ends, since a test runner reads it once.

### 2. Steps side by side

`checks.side_by_side`, off unless set:

```json
"side_by_side": {"max_steps": 4}
```

With it, a run starts each step that applies as soon as the steps it
waits for have finished, keeping at most `max_steps` running at once.
A step's own new keys:

| key | meaning |
| --- | --- |
| `after` | the names of steps this one waits for. A name that doesn't apply to the head is ignored. |
| `alone` | `true`: a timing. It runs after every step that isn't `alone` has finished, with nothing else of its run beside it, the `alone` steps one at a time in the configured order. |
| `light` | `true`: the step uses about one CPU, waiting on something else. It gets `CHECK_CPUS=1` and isn't counted when the others share the run's CPUs. A step that is CPU-bound on some heads, such as one that first builds what a change touched, isn't `light`. |
| `caches` | per-step overrides of `checks.caches`, e.g. `{"CARGO_TARGET_DIR": "cargo-target-wasm"}`, for a step that would otherwise wait on another step's build lock. Each such directory is per slot, like the shared ones, and `cache_limit` and pruning cover it. Every step that builds the same thing needs the same override, or it builds it again, cold, in the default directory. |

A step with no `after` starts at once, and without `side_by_side`
these keys mean nothing. A step whose `after` makes a cycle is a
configuration error, reported when the configuration is read.

**Steps that write the same files.** A run's steps share the head's
worktree. Two steps that write the same files there (the same `make`
target, a generated directory) must not overlap, so the later one
names the earlier in `after`. That holds for `alone` steps too: their
order alone keeps them apart today, but `after` says why, and survives
a reordering.

**CPUs within a run.** The run's share (from 1, at the moment a step
starts) is divided by the steps of the run that are running and not
`light`, this one included, and is at least 1. So two CPU-bound steps
side by side each get half of what one would get alone, rather than
each getting all of it and the host being oversubscribed by the run
itself.

**What stays the same:**
- Every step that applies runs, even if another failed, and none is
  retried.
- Each step has its own scope, memory limit, CPU weight, timeout, log,
  results directory and trace. Peak memory goes up, as more steps run
  at once: up to `max_steps` times `memory_max` a slot.
- The verdict lists the steps in the configured order, not the order
  they finished. The first failed step it names is the first in that
  order.
- A step's CPU time is now its own process tree's, from that process's
  resource usage when it is reaped (`os.wait4`), since the run's
  children-wide counter can't tell steps that overlap apart.
- Reuse (designs/0009) and the fast lane are unchanged. A reused step
  isn't run, and a step waiting on it starts at once.

**Timings.** An `alone` step has nothing of its own run beside it.
It still shares the host with other slots' runs, which is how every
step runs today. A project whose timings need a quiet host already has
`quiet` holds (designs/0020) for that.

**Step alerts.** Side by side, a step's wall includes waiting on a
build lock or sharing CPUs with its neighbours, so it isn't comparable
with the same step run alone. Each verdict records `"side_by_side":
true`, and `step_alert` (designs/0019) compares a step only with runs
in the same mode. The first days after the switch therefore raise no
alert.

### Example

A project with a CPU-bound test step, a web build, two container
steps, a browser suite and two timings:

```json
"cpu_share": "step",
"side_by_side": {"max_steps": 4},
"steps": [
  {"name": "web dependencies", "run": ["npm", "ci"], "always": true},
  {"name": "fmt-check", "run": ["make", "fmt-check"]},
  {"name": "vet", "run": ["make", "vet"]},
  {"name": "test", "run": ["make", "test"]},
  {"name": "test-web", "run": ["make", "wasm", "test-web"],
   "after": ["web dependencies"],
   "caches": {"CARGO_TARGET_DIR": "cargo-target-wasm"}},
  {"name": "desktop", "run": ["make", "test-desktop"], "light": true},
  {"name": "browser", "run": ["make", "test-browser"],
   "after": ["test-web"], "alone": true,
   "caches": {"CARGO_TARGET_DIR": "cargo-target-wasm"}},
  {"name": "timing", "run": ["make", "test-timing"], "alone": true}
]
```

`fmt-check`, `vet`, `test`, `web dependencies` and `desktop` start
together, up to four at once, and the rest as places come free.
`test-web` starts once `web dependencies` is done, building in its own
target directory. `browser` and `timing` run last, one after the
other, with nothing else of the run going. `browser` builds the same
web build as `test-web`, so it names it in `after` and uses its
directory.

## Not changed

- The number of slots, and how a head is chosen for one.
- Where a step runs: still in the head's worktree, shared by its steps
  (see "Steps that write the same files").
- Anything a project hasn't switched on.

## Rollout

1. Release the code with both switches off.
2. The project turns on `cpu_share: "step"` first, and compares a day's
   walls and the host's load with the day before.
3. It then turns on `side_by_side`, with `after`, `alone`, `light` and
   `caches` set by whoever owns the steps, and compares again.

Two things to expect at step 3:
- Steps that share a build directory wait on its lock, and overlap
  only their run time. Each step's log shows the wait, so a project
  can decide from the logs whether a separate directory, which costs
  disk and a cold first build in each slot, is worth it.
- Tests that time out under load fail more often, since a slot's
  steps load the host more than one step did. They are the project's
  to fix.

## Amendment (2026-10-26): a step's share is never less than its run's

* Status: proposed

**Why.** With `cpu_share: "step"` turned on, a day's long test step was
slower at every level of load than the day before. CPU seconds per
test were the same, so no test got dearer, and the host wasn't
oversubscribed. What changed was how many CPUs the step ran on: about
4.4 over its testing wall, against about 7.5 before.

The run's share and a step's share use the same rule, from the slots
busy at the time. The run's is taken as the run starts. A long test
step's is taken after the build, by which time the other slots have
usually filled. On a host that is nearly always busy, the step's share
therefore comes out lower than the run's. Section 1 meant to give a
late step the CPUs that finished runs let go, not to take CPUs from it.

**Change.** With `cpu_share: "step"`, a step's `CHECK_CPUS` is the
larger of the run's share, taken as the run starts as with `"run"`,
and the share worked out as the step starts. A step never gets fewer
CPUs than `"run"` would have given it, and still gets more when slots
have emptied since its run began. A host that sets `CHECK_CPUS` itself
still wins, and side by side, the steps running still split the share.

No new key. Rollout: release the change, then compare a day with
`"step"` against a day with `"run"` again.
