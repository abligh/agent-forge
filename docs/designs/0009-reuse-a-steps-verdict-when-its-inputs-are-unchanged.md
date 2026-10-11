# 0009: Reuse a step's verdict when its inputs are unchanged

* Status: approved (Alex, 2026-10-11, on the issue; he confirmed in
  session that it covers this design,
  <https://github.com/abligh/agent-forge/issues/9#issuecomment-6110608808>)
* Author: XMPPEngineer
* Date: 2026-10-11
* Issue: <https://github.com/abligh/agent-forge/issues/9>

## Summary

With `checks.depends` (design 0005), a pull request's head runs only
the steps that read what it changed. A step it does run is often run on
inputs that an earlier green run already tested. Examples are a second
push that only changes a document, the Merger's update of the head, or
two pull requests that both touch one crate. On one project, 37% of a
day's runs repeated the inputs of the expensive step exactly.

`merge.test_ahead` already reuses a whole verdict when the tree is the
same (`MATCHED`). This proposes the same thing for each step:
`checks.reuse`. A step whose input digest equals the digest in a green
verdict here, recent enough, isn't run. Its green is carried over, and
the description says where from.

## Goals

* **A step never runs on inputs it has passed on.** This holds for
  every head, not only one whose whole tree matches.
* **A reuse is never wrong in a way a run wouldn't be.** Anything the
  digest can't see makes the step run.
* **Every reuse can be traced.** The record and the status say which
  run is reused.

## Non-goals

* **Reusing red.** A red step always runs again.
* **Caching build output across hosts.** That is a build cache's job.
  This decides only whether to run.

## Design

### A step's input digest

The tested tree is the head's, or with `merge_default` (design 0007)
the merged tree. For a step whose read set the dependency record gives
(design 0005), the digest is a SHA-256 over:

1. the blob id and mode, at the tested tree, of each tracked path in
   the step's read set, or "absent";
2. for each directory the step listed, the sorted names of its entries
   at the tested tree;
3. for each path the step looked for and didn't find, whether it
   exists at the tested tree;
4. the digests of the earlier steps whose outputs it read (a path in
   its read set that an earlier step wrote);
5. `CHECK_BASE`'s tree, when the step reads `CHECK_BASE` (the record
   notes that it does);
6. the step's argv and `checks.steps` entry;
7. the output of the step's `version` command, when it has one: a
   command the project declares, such as `rustc -vV`, a runtime's
   `--version`, or the versions of system packages a test loads.

### When a step is reused

A step is reused when:

* the newest verdict here with the same digest for that step is green
  for that step;
* that verdict is at most `max_age_days` old (default 7);
* it ran on this host, with the same steps digest.

### When a step always runs

* it is `always`;
* its read set includes an untracked path that no earlier step wrote;
* its `version` command fails, or the record has no read set for it;
* the run that would be reused ended uncleanly or timed out.

### What is recorded and posted

Each step's digest is kept in the verdict. A reused step is recorded
as `{"reused": <sha>, "pr": N}` with 0 seconds. The description names
the first reuse: `green on <host>: test-rust as #458 at 3193656, same
inputs`. A reuse of a reuse points at the original run, and ages from
it, like `MATCHED`.

### Interaction with other checks

* **`MATCHED`.** A whole-tree match is tried first, as now. Reuse
  applies to the steps of a run that isn't matched.
* **The fast lane.** It estimates with reused steps at zero seconds, so
  a head whose expensive steps are all reused is cheap.
* **Runs of the default branch.** These never reuse. They are what the
  dependency record and the digests come from.

## Config shape

```json
"checks": {
  "depends": true,
  "reuse": {"max_age_days": 7},
  "steps": [{"name": "test", "run": ["make", "test"],
             "version": ["sh", "-c", "rustc -vV; node --version"]}]
}
```

`reuse` is refused without `depends`. `version` is optional on any
step.

## Trade-offs and failure modes

* **A missed input reuses a stale green.** Examples are an environment
  variable, a network fetch, or a file read through a path the tracer
  can't see. The age limit bounds how long that lasts. Runs of the
  default branch never reuse, so a stale green is found there at the
  next merge. Steps that read outside the tree must declare `version`
  or be `always`.
* **Digesting costs time.** Each run takes one `git ls-tree` per step
  over its read set: milliseconds against minutes.
* **Harder to read.** A run of three steps of nine, two of them reused,
  needs a clear record. The log line lists each step as ran, skipped
  or reused.

## Testing strategy

agent-forge's tests cover:

* each digest input changing the digest;
* reuse of a green step;
* red, an unclean exit, a timeout, age and another host never reused;
* an untracked read, a failing `version` and `always` running;
* an earlier step's output feeding a later digest;
* the merged tree used with `merge_default`;
* the record and the description;
* the fast lane's estimate;
* the default branch never reusing;
* `reuse` refused without `depends`.

## Implementation plan

1. `checks.reuse` and the per-step `version`, off by default, in one
   release.
2. A project turns it on in its configuration, in a pull request its
   owner merges.
