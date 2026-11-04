# 0045: A page that says why merging is slow

* Status: approved (Alex, 2026-11-04, in XMPPEngineer's session)
* Author: XMPPEngineer
* Date: 2026-11-04

## Summary

The tools raise alerts when something fails. They don't say why fewer
pull requests are merging than usual. Today every "why have we only
merged 4 things in the past hour?" needs someone to run a report by
hand, and then to read the build check's and the Merger's logs side by
side.

This design adds `forge-flow`. It runs from cron every 5 minutes, reads
the records the build check, the Merger and the router already keep,
and writes one static web page, with every number also in a JSON file.
The page answers the question in its first panel: for each hour, how
many minutes the Merger spent merging, and how many it spent waiting,
and on what. The other panels say whether we are optimising the right
thing. The owner's objective is many merges an hour and a short wait
for each pull request, treated as one goal.

Three new `stats_watch` figures can raise an issue from the same
numbers. They are off until a project names them.

## Evidence

A review of one project's pipeline over three days needed nine
scripts, run by hand, over the build check's and the Merger's logs.
Over the worst 15 hours it found:

* 7.6 merges an hour, against 10.4 two days before;
* 5.9 build-check runs for each merged pull request, from its first
  push to its merge, against 3.0, and 4.1 runs per merge in the
  period, against 2.4;
* 2.3 pull requests merged on each green run, against 3.1;
* of 54 green batch tops, 25 merged none of their batch;
* 1.4 hand-backs told per merge, against 0.36, and a third of them
  repeats for the same head;
* the median wait for a build-check slot was under 2 minutes, so build
  capacity was not the limit;
* one reviewer was busy 92-100% of the time for two days, while
  another was busy 12-22%.

None of this was visible without the scripts. Each finding is a
figure this page now shows, computed the same way.

## Design

### What it reads

From the project's state directory (`~/.local/state/agent-forge/<project>/`):

| File | What it gives |
|---|---|
| `pr-check/worker.log` | each run's start, slot and kind: a head, a fast-lane head, a prediction (`ahead`) or the default branch |
| `pr-check/verdicts.log` | how each run ended (`VERDICT`, `MAIN-GREEN`, `MAIN-RED`, `STOPPED`), and greens posted without a run (`MATCHED`, `COVERED`) |
| `merger/merger.jsonl` | one record a Merger tick: queue, batch, later batches, decisions, held, overdue, right of way |
| `merger/ahead.json` | whether a culprit search is on now |
| `merger/handed-back.json` | why each pull request now with its author went back |
| `router/presence.jsonl` | who was busy and idle, if the router records it |

A run's end is the first end logged for the same kind, pull request and
commit after its start, within 3 hours. A worker that found its tree
already green and posted without building (`MATCHED`, `COVERED`, or
`MAIN-GREEN ... matched`) is not counted as a run. A run with no end
logged held its slot until the next run in that slot, for at most an
hour.

It reuses `forge-stats`' time parsing, statistics, verdict patterns and
hand-back causes, so the two tools agree.

### The panels

Each panel covers the last 1 h, 6 h and 24 h, and the 7 days up to
now as a baseline, or as much of them as the records hold.

1. **Why so few merges.** Each Merger tick is put in one state, and the
   time to the next tick counts to it, for at most 10 minutes; the
   rest is "Merger not ticking". The states, first match wins:
   * `merging`: the tick merged;
   * `culprit search`: a batch top of two or more went red, and the
     queue's first pull request is still one of that batch's members;
   * `waiting on a batch top`: the tick waits on a pull request in a
     batch of two or more;
   * `waiting on a head check`: it waits on a head outside such a
     batch, or updated one;
   * `handed back`: every eligible pull request is back with its
     author;
   * `eligible but held`: approved pull requests, all of them held;
   * `nothing eligible`: no approved pull request.

   A stacked column for each of the last 24 hours shows the minutes in
   each state. This is the panel to read first.
2. **Flow.** Merges and arrivals each hour. An arrival is a pull
   request's first build-check run. The mean number eligible and held.
   Where each waiting pull request is at the last tick: in the batch,
   in a later batch, queued, back with its author, or held, by kind of
   hold.
3. **Efficiency.**
   * (a) Runs per merged pull request, from its first push to its
     merge: its head runs, plus the batch runs it was a member of.
     Mean, median and p90, and the split between the two.
   * (b) Every run started in the period (heads, predictions and the
     default branch), per merge.
   * Pull requests per merge slot. A merge slot is the green run a
     merge rested on: the head's own run, the prediction it was
     `MATCHED` to, or the batch top that `COVERED` it. The slots are
     sized 1, 2-3 and 4+.
   * Green batch tops on which all, part or none of the batch merged.
4. **Rework.** Hand-backs the Merger told, per merge, by cause, with
   repeats apart: a repeat is a hand-back told again for a head
   already handed back. Head checks that were green, `red(merge)` (a
   conflict with the default branch), or red for another reason.
5. **Latency.** First approval to merge, median and p90. Locally, the
   first approval is the first tick that listed the pull request as
   queued or held, since the Merger lists only approved ones. With
   `--github`, the tool also asks GitHub for each merged pull request's
   first approving review. Answers are kept in `approvals.json` in the
   output directory, so each pull request is asked about once. The
   page works without it.
6. **Capacity.** Slot-hours by what they went on: green and red heads,
   green and red batch runs, the default branch, stopped or superseded
   runs, reruns of a commit already run, and the fast lane. These are
   set against the slots times the period. Also the median wait for a
   slot, the median run time, and how busy each agent was, reviewers
   first. An agent is busy while the router says `busy`; `idle`,
   `shell` and anything else count as not busy.
7. **Policy.** The right-of-way holder now and since when, and the
   share of ticks with right of way held. The overdue count now, and
   its mean. Batch re-cuts an hour: a re-cut is a tick whose batch is
   not the last tick's batch less what merged.

### Output

`forge-flow --state-dir DIR --out DIR` writes:

* `index.html`: one static page. It has no scripts, and its charts are
  inline SVG and CSS bars. Each bar has a tooltip with its value, and
  every chart has a table beside it. It is readable on a phone, in
  light and dark.
* `flow.json`: every number on the page.
* `flow-history.jsonl`: one line for each whole hour, appended once.
  The first run fills in up to 7 days.

Each file is written to a temporary name and renamed, so a server
never serves half a page. Without `--state-dir`, the configuration
gives the project and its state directory. With a configuration, the
slot count is `checks.slots` and the reviewers are the agents of kind
`reviewer`. Without one, the slots are the most seen in the last 7
days, and no agent is marked as a reviewer.

### Running and serving

From the build-check host's crontab, through `forge-cron-run`:

    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-flow --out $HOME/flow/widget

Serving the page is the host's business, not agent-forge's. Point a
static file server at the `--out` directory, behind authentication. For
example, with Caddy:

    example.org {
        handle_path /flow/* {
            basicauth {
                owner <hash from caddy hash-password>
            }
            root * /home/widget/flow/widget
            file_server
        }
    }

### Alerts

Three new figures for `stats_watch` (designs/0024), computed by
`forge-flow` in `forge-stats-watch`'s own process. Like every figure,
each is off until the configuration names it:

| Figure | What it is | Out of line when |
|---|---|---|
| `hand_backs_per_merge` | hand-backs told per merge | over `limit`, or over `ratio` × the baseline's |
| `prs_per_slot` | pull requests merged per green run they rested on | under `limit`, or under the baseline's over `ratio` |
| `reviewer_busy` | the busiest reviewer's share of the window busy, in percent | over `limit` |

`hand_backs_per_merge` and `prs_per_slot` are judged once there are
`min_samples` merges and merge slots. `reviewer_busy` counts only
reviewers the router listed for at least 90% of the window, and
`min_samples` doesn't apply to it. The window is the watch's
`window_hours`, 6 by default. For example:

```json
"stats_watch": {
  "owner": "Engineer",
  "figures": {
    "hand_backs_per_merge": {"limit": 1},
    "prs_per_slot":         {"limit": 2},
    "reviewer_busy":        {"limit": 90}
  }
}
```

## Limits

- **Culprit search is inferred.** The Merger's record doesn't say when
  a search is on, so a search is taken to last from a red batch top
  until the queue's first pull request is none of that batch's
  members. `ahead.json` gives the exact answer for the last tick
  only.
- **Batch membership is inferred** from the tick a prediction started
  under, so (a) and the batch tops may be off by a few percent.
- **The local first approval is when the Merger first saw it**, so it
  is late by up to a tick, and an approval given while the Merger was
  stopped is late by the stop. `--github` gives the real time.
- Open pull requests that were never approved aren't in the Merger's
  records, so the flow panel's "where they wait" starts at approval.
- It reads only this host's records. A second check instance on another
  host (designs/0021) is not counted.

## Testing

Unit tests drive it with small synthetic logs: run starts and ends,
including stopped, matched, rerun and still running runs; each tick
state, the not-ticking gap and an inferred culprit search; merge slots,
batch tops, runs per merged pull request, hand-back repeats, local and
GitHub latency (with a fake GitHub and its cache), capacity, presence
and re-cuts; the snapshot of where pull requests wait; and the files
written, including that the history adds no hour twice. The watch's
tests cover the three new figures and the call into `forge-flow`.

## Rollout

1. Implement and release it.
2. A project pins that release and adds the cron line on its
   build-check host. The host's owner points a web server at `--out`.
3. Later, if wanted, the project names the new figures in
   `stats_watch`.

## Amendment (2026-11-04): per hour, and graphs

The owner asked for the page to do the arithmetic: every count in the
flow panel per hour, graphs of the last 24 hours, the two
builds-per-pull-request figures named plainly beside their targets,
and the build check's run time over a longer period.

* **At a glance.** A panel above the first says, for the last 1 h,
  6 h, 24 h and the 7 days, merges per hour against the bar of 10, and
  the two builds-per-pull-request figures with their targets:
  * "Builds per merge (all build runs ÷ PRs merged)", target about
    1.1: every run started in the window, whatever it was for, over
    the merges in it. This is (b).
  * "Builds per merged PR along its own path", target about 2: for
    each pull request merged in the window, its own head runs from its
    first push and the batch runs it was in, up to its merge. This is
    (a).

  Both were already computed as these names say, so neither changed.
* **The flow panel is per hour.** Merges, arrivals, runs started,
  hand-backs told and batch re-cuts show per hour in every window, the
  count itself in a tooltip. The means stay means. `flow.json` adds
  `arrivals_per_hour`, `runs_per_hour` and `hand_backs_per_hour`
  beside the counts, which are kept.
* **Graphs of the last 24 whole hours.** Merges each hour, as columns
  against a line at 10; builds per merge, builds per merged pull
  request and pull requests per merge slot, each its own graph with
  its target, since their scales differ; and first approval to merge,
  median and p90. An hour with no Merger record is a gap, never a
  zero; a ratio's hour with no merge is a gap too. `flow.json` adds
  `graph_hours`.
* **Build time over 7 days.** The run time of runs that built to a
  verdict, mean and median, in 6-hour buckets, heads and batch runs
  apart. Hour by hour it would be too few runs a point. `flow.json`
  adds `build_times`, and each window adds `run_seconds.mean` and
  `run_seconds_by_kind` (head, batch and the default branch).
* **The history keeps more.** Each new line of `flow-history.jsonl`
  also has `runs_per_merged_pr`, `approval_to_merge`, `run_seconds`
  and `run_seconds_by_kind`. Lines already written are left as they
  are.

The graphs are inline SVG drawn in Python, as the page allows no
script: each has a title and a description for screen readers, a
tooltip on every hour and a table of its values beside it. They are
360 units wide, in a grid that puts one a row on a phone.

A page rendered from a saved `flow.json` written before this change
takes the graphs from `flow-history.jsonl` instead (`with_graphs`),
where an hour with no line is a gap and figures older lines lack are
left out.
