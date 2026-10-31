# 0040: A green nightly wakes the issues waiting for it

* Status: approved (Alex Bligh, 2026-10-31, https://github.com/abligh/agent-forge/issues/180#issuecomment-6543862165)
* Implemented: v0.4.139, off until configured; the amendment below,
  next release.
* Author: XMPPEngineer
* Date: 2026-10-31

## Summary

Some projects close an issue only after checking it on a nightly build,
since the nightly makes builds the build check doesn't. While that is
all that is left, the owner labels the issue as waiting for the nightly
and sets `Defer-until:` to after the next scheduled nightly, so the
router doesn't wake her before there is a build to check.

The scheduled run isn't the only nightly. designs/0028 runs a red
nightly again every few hours, and a run publishes its builds whether
it ends green or red (see the amendment). An issue deferred to
tomorrow's scheduled run waits all day for a build that already
exists.

With `router.nightly_wake`, the first nightly run that published its
builds with an issue's fixes in them wakes its owner, whatever its
`Defer-until:` says.

## What happened

On one project the nightly was red for most of a day and run again
every four hours. Fourteen issues were labelled as waiting for the
nightly, each deferred to the next scheduled run at 03:17. When a
rerun turned green in the afternoon, nobody was told; the issues would
have waited until the next morning. A person asked why agents weren't
checking the nightly build.

The same day one owner had nine issues labelled as waiting for the
nightly, without `Defer-until:`. They were her oldest, so the idle offer
and the merge notice kept naming one of them as her next work; she
answered that there was nothing to do until the nightly, and was never
offered the issue she could have worked on, whose blockers had all
closed.

## Design

A new setting, `router.nightly_wake`: `{"label": "awaiting-nightly"}`,
the label that marks an issue as waiting for a nightly check (required).
Off without it. It needs the `nightly` section (designs/0028), whose
state says which run last ended green.

**Which build.** forge-nightly's `nightly.json` keeps the last green run
(`green`: `run`, `sha`, `ended`). forge-nightly now keeps the run's
`started` there too, as it already does for a red run; a green record
without it wakes nobody. The router reads the file as designs/0039's
lookup reads the red one: while it is fresh.

**Which issues.** An open issue carrying the label, with an owner, not
parked by a waiting or quiet label, and labelled before the green run
started. An issue is labelled once its fixes have merged, so a label
older than the run's start means the fixes are in the build, which
was made from the default branch's head when it started. The label's
time is the newest `LabeledEvent` for it among the issue's last 30
label events, which the router fetches whenever `nightly_wake` is set;
none found there wakes nobody. The label itself doesn't park an issue,
even when it starts `Status:waiting-`.

**Not actionable meanwhile.** An issue carrying the label waits, as a
waiting label makes it wait, with or without `Defer-until:`: the idle
offer (rule 15), the merge notice's next issue (rule 17) and the
hand-over count (designs/0038) pass over it. Rule 22 is what wakes it.

**Rule 22.** For each such issue, its owner's turn, once for that run
(the event is the run's end, never reminded), even when `Defer-until:`
is still to come (no rule that decides an issue's turn reads
`Defer-until:`; only the idle offer does):

> #N waits for a nightly check, and nightly run R (abc1234, builds
> published at 14:05Z) includes its fixes: check it on that build now.

The issue's own decision (rules 9-20) still stands: rule 22 is a
second decision beside it, not a rule in its order. In that order it
would either hide a comment rule 12 would wake her for, or be hidden
by it, and the turn it hid would be forgotten and said again later.

A later green run wakes her again while the label stays on. So she
takes the label off once she has checked, closing the issue or not,
and puts it back when a new fix merges; a green run that started
before that doesn't wake her.

## What it costs

- A wake a deferred issue wouldn't otherwise get, once per green run.
- An issue labelled after its fixes merged but before a run started
  that was dispatched from an older head would be woken early. The
  router can't see that cheaply; the owner sees it on the build and
  waits.
- An issue left labelled while a further fix merges keeps its old
  label time, so it can be woken for a build without that fix. The
  owner takes the label off while a fix is in flight, or labels it
  again once the fix has merged.

## Rollout

Off by default. A project sets `router.nightly_wake` after a release
with this is installed. Measured after a week: hours from a green
nightly to the issues waiting for it being closed or relabelled.

## Amendment, 2026-10-31: red runs publish builds too

The design assumed only a green nightly makes builds to check. It
doesn't: the nightly's release jobs depend on the builds, not on the
tests, so a run that fails its tests still publishes. On the day this
shipped, run 41190157895, which started at 09:48Z, failed its
`build-and-test` and `test-mac` jobs, but its `release` and
`release-mac` jobs succeeded, and the published nightly release
pointed at its commit. An issue labelled at 01:38Z could have been
checked on the 03:17Z and 09:48Z builds; rule 22 waited for a green
run.

So rule 22 reads the newest run that published its builds, red or
green:

- A new key, `nightly.release_jobs`, lists the jobs that publish (by
  name, or a matrix's `name (...)`). A judged run in which each of
  them ran and succeeded published its builds, whatever the deciding
  job says, and forge-nightly keeps it in `nightly.json` as
  `published` (`run`, `sha`, `started`, `ended`). `green` is kept as
  before.
- Without `release_jobs`, only a green run counts as published, so
  nothing changes for a project that doesn't set it.
- The router reads `published`, else `green` (a file written before
  forge-nightly kept `published`), and its message names the run's
  end as when its builds were published, not when it went green.
