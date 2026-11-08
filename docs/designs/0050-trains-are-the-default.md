# 0050: Trains are the default

* Status: accepted
* Author: XMPPEngineer
* Date: 2026-11-08

## Summary

Testing ahead (`merge.test_ahead`, designs/0001), batches
(designs/0032, 0035) and trains (designs/0047) were each off until a
project turned them on. Every project that uses agent-forge now runs
them, in trains, and a new project should get the same without having
to find and set five keys. So:

1. **With no `merge.test_ahead` key, testing ahead is on** whenever the
   project has a `checks` section (the build check that runs the
   predictions) whose `merger_first` isn't false. It is then as if the
   project said `{"depth": 7, "batch": 4, "batches": 2, "trains":
   "live"}`.
2. **`false` turns it off.** `true` turns it on with the defaults, as
   `{}` does.
3. **An object takes the defaults for what it leaves out**, fitted to
   what it gives: `depth` 7; `batch` 4, or `depth` + 1 if that is less;
   `batches` as many as fit `batches` × `batch` ≤ `depth` + 1, up to 2
   (1 when `batch` is 1); `trains` `"live"` while `batch` is above 1
   and `fixed_order` isn't false.
4. **`"trains": "off"`** keeps batches without trains.

The owner asked for this on 2026-11-08, in XMPPEngineer's session:
"Ensure trains is the default."

## Why these numbers

Depth 7, batches of 4, two in flight, is the smallest shape every
project that runs trains settled on: `batches` × `batch` is then
`depth` + 1, so the second train is always fully predicted. A larger
queue can raise `batch` and `depth`; a small one loses nothing, since a
train of one is a single pull request's run.

## What changes for a project

Nothing, for one that sets `test_ahead` with `batch`, `batches` and
`trains`. A project that set `test_ahead` to an object without `batch`
had batches of one and no trains, and now gets batches of up to 4 in
trains; one without the key and with a build check now tests ahead.
Either says `false`, `"batch": 1` or `"trains": "off"` to keep what it
had.

Testing ahead needs what it always needed: branch protection that
requires a pull request to be up to date before it merges (otherwise
there is no wait to remove), and the Merger and the build check sharing
one checkout. A project without a `checks` section, whose statuses come
from elsewhere, is not changed.

## Implementation

`agent_forge.config.test_ahead(data)` returns the settings that apply,
or None, and is the only reader: `forge-merger`, `forge-pr-check` and
`forge-config check` (which checks what applies, defaults included)
all use it.
