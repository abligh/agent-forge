# 0021: A second check instance, on another platform

* Status: approved (Alex Bligh, 2026-10-20, https://github.com/abligh/agent-forge/issues/34#issuecomment-6295002798)
* Author: XMPPEngineer
* Date: 2026-10-20

## Summary

A project's build check is one `checks` section: one status context,
one list of steps and one set of resources, run by `forge-pr-check` on
one host. A project that also wants a check on another platform, such
as macOS, has no way to say so. A second instance on that platform
would read the same section, and would run the first host's steps
under the first host's context.

This design adds `checks.instances`: named blocks, each describing one
more instance of the check. An instance picks its block with
`--instance <name>` or `$FORGE_PR_CHECK_INSTANCE`, and gets:
- its own context, steps, host name and poster;
- its own slots and resources;
- its own state directory;
- none of the first instance's tracer-based settings unless it asks
  for them.

With no instance selected, `forge-pr-check` runs exactly as today, so
an existing host is unchanged.

## Evidence

- **No second context.** `checks.context` and `checks.steps` are single
  values, and the state directory, the log lines and the posted status
  all follow from them. A project that wants a macOS check today would
  have to keep a second configuration by hand.
- **The rest already runs elsewhere.** Read with a macOS host in mind:
  - The CPU count falls back from `sched_getaffinity` to
    `os.cpu_count()`.
  - When no systemd user scope can start, `memory_cap` prints one
    `UNCAPPED` line per run and runs the steps without a scope.
  - Everything else is git, the GitHub API and Python's standard
    library.
- **What doesn't run elsewhere is optional.** The tracer (`depends`)
  needs `LD_PRELOAD` or `strace`. macOS has neither for this use:
  System Integrity Protection strips `DYLD_INSERT_LIBRARIES` from
  protected binaries. `fast_lane`, `reuse` and `fresh` all need the
  tracer, and the configuration already refuses them without
  `depends`. The only problem is that a second instance would inherit
  them.

## Design

### `checks.instances`

```json
"host": "build-1",
"checks": {
  "context": "ci/build-and-test",
  "steps": [ ... ],
  "depends": true,
  "fast_lane": {"max_seconds": 600},
  "instances": {
    "mac": {
      "host": "mac-1",
      "context": "ci/mac",
      "steps": [{"name": "mac", "run": ["make", "mac"]}],
      "slots": 1,
      "memory_max": null,
      "poster": "MacChecker"
    }
  }
}
```

**Selecting an instance.** `--instance <name>`, else
`$FORGE_PR_CHECK_INSTANCE`, selects `checks.instances.<name>`. An
unknown name is refused at start, with the names that exist. It
doesn't fall back to `checks`, because that would post the first
context's statuses from the wrong platform.

**Each key comes from one of three places:**

| Keys | Where they come from |
|---|---|
| `host`, `context`, `steps`, `poster` | The block. `context` and `steps` are required. `host` falls back to the top-level `host`, and `poster` to `checks.poster`. |
| `slots`, `memory_max`, `cpu_weight`, `step_timeout_minutes`, `caches`, `cache_limit`, `keep_ignored`, `drop_env`, `merge_default`, `red_comment`, `max_wait_minutes` | The block, else `checks`, else the default. `memory_max: null` means no scope, and no `UNCAPPED` line. |
| `depends`, `tracer`, `depends_minutes`, `fast_lane`, `reuse`, `fresh`, `main` | The block only, else the default. They aren't inherited, because they describe the first host's tracer. So a block runs every step on every head, and never runs the default branch on its own, unless it says otherwise. A block on a host where the tracer works can turn them on. |
| `main_red_issue`, `first_authors`, `merger_first` | `checks` only. They belong to the context the Merger requires, so a block can't set them. |

Any other key in a block is refused, so a typo can't pass silently.

### State and logs

**State directory.** Each instance keeps its own,
`<state>/pr-check-<name>`. Two instances never share a record, a lock
or a slot, even on one machine.

**Log lines** carry the instance's name, for example
`GREEN mac <pr> <sha>`. Messages already say " on <host>", now with the
block's host. `forge-pr-check status --instance mac` reads that
instance's state.

### Posting and the Merger

**Each instance posts its own context** as its block's `poster`.

**A separate App for a separate host.** A second platform's machine is
often less guarded than the first, for example a machine on a home
network rather than in a data centre. An App with only the
commit-statuses permission puts no key on that machine that can merge
or push. The documentation recommends one, and the design doesn't
require it.

**The Merger** still requires `checks.context` alone. Requiring an
instance's context as well is a later, explicit setting, not part of
this design. Until then a red status from the new instance is
information for the head's author, and can't stall merges, so a new
platform's check can prove itself first.

## Limits

- **The tracer stays Linux-only.** Without it an instance runs every
  step on every head. That suits a short platform build, not a full
  test suite on a slow host.
- **Not required by the Merger** until a later setting says so.
- **One project per instance,** as today.
- **What starts it is the host's business.** On macOS that's launchd
  with a `StartInterval`, or cron. The instance doesn't care which.

## Testing

**Unit tests:**
- A block's keys override `checks`' where they may.
- The tracer keys aren't inherited.
- The project-wide keys are refused in a block, and so is an unknown
  key or an unknown instance name.
- `memory_max: null` starts no scope and prints nothing.
- The context, host, poster and state directory follow the block.
- With no instance selected, every setting, path and log line is as
  today, compared against a captured run.

**One real run** on a macOS host: a block with one step, against a
throwaway pull request. It posts that block's context and nothing else.

## Rollout

1. Implement in an agent-forge release. No project changes behaviour
   until it adds a block.
2. A project adds a block and its App, and starts the instance on the
   new host. The Merger ignores the new context.
3. Once the new instance has run reliably, a later design decides
   whether the Merger requires it.
