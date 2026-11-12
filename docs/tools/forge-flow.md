# forge-flow

`forge-flow` writes a static web page that says why merging is slow
([designs/0045](../designs/0045-a-page-that-says-why-merging-is-slow.md)).
It reads the records `forge-pr-check`, `forge-merger` and
`forge-router` already keep on the build-check host, and shows, hour by
hour, what the Merger spent its time on (merging, waiting on a check,
handed back, nothing eligible), how many build runs each merge cost,
where pull requests wait, and how busy the slots and the reviewers
were. It changes nothing but its own output. A project wants it
because "merges are slow" has many causes, and each needs a different
fix; this page says which one it is.

## Usage

    forge-flow [--state-dir DIR] --out DIR [--now TIME] [--github]
               [--recent-trains N] [--checkout DIR]

Run it every five minutes from the build-check host's crontab, through
`forge-cron-run`:

    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-flow --out $HOME/flow/widget

Serving the page is the host's business. Point any static file server
at the `--out` directory, behind authentication, such as Caddy's
`file_server` under a path with `basicauth`.

To read old records as they stood at some time, run it by hand with
`--now` and an `--out` of its own.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--out DIR` | required | Where `index.html`, `flow.json` and `flow-history.jsonl` are written (and `approvals.json` with `--github`). Created if missing. |
| `--state-dir DIR` | `~/.local/state/agent-forge/<project>/` | The project's state directory, the parent of `pr-check/`, `merger/` and `router/`. With it, the page can be made without a configuration (the project's name is then the directory's). |
| `--now TIME` | now | Read the records as at this time, ISO 8601 in UTC, such as `2026-10-12T09:00Z`. |
| `--github` | off | Also ask GitHub for each merged pull request's first approving review, for the latency panel, and for the open pull requests carrying `labels.urgent`, for the priority marks. Needs the configuration. |
| `--recent-trains N` | 8 | How many of the last trains "Recent trains" tells of. |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT`, else the current directory | Where the configuration is found. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Find the configuration ([config.md, Finding it](../config.md#finding-it)). |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the state directory ([config.md, Where state lives](../config.md#where-state-lives)). |
| `FORGE_AGENT` | With `--github`, the App whose token reads GitHub. When it doesn't mint, `GH_TOKEN` is used. |
| `GH_TOKEN` | With `--github`, the token to read with when `FORGE_AGENT` gives none. With neither, it reads anonymously, which a public repository allows at a low rate. |
| `FORGE_GITHUB_API` | GitHub's API base (default `https://api.github.com`). |

## Configuration

It needs no section of its own
([config.md, The flow page](../config.md#the-flow-page)). From the
configuration it reads the project's name, `repo` (for `--github`),
`checks.slots` (the build check's slots, for capacity) and which agents
are of kind `reviewer`, with their session names, for how busy
reviewers were. A configuration that can't be read is an error unless
`--state-dir` is given without `--github`.

## What it does

**What it reads**, all under the state directory:

| File | What it gives |
| --- | --- |
| `pr-check/worker.log` | The line that starts each build-check run: its slot, and `ahead` for a Merger prediction. |
| `pr-check/verdicts.log` | How each run ended (`VERDICT`, `MAIN-GREEN`, `MAIN-RED`, `STOPPED`), and verdicts posted without a run (`MATCHED`, `COVERED`). |
| `merger/merger.jsonl` | One record per Merger tick. |
| `merger/ahead.json`, `merger/handed-back.json` | The batch being tested ahead, and the hand-backs, now. |
| `router/presence.jsonl` | Who was busy and idle, if the router recorded it. |
| `nightly/nightly.json` | The pull requests fixing a red nightly, while forge-nightly's file is fresh and its issue open. |
| `pr-check/state.json` | The pull requests fixing a red default branch (`main_red.fixes`), while its issue is open. |

**The page.** One static HTML file, with no scripts: inline SVG and CSS
bars, light and dark, readable on a phone. Its hourly graphs cover the
last 24 whole hours; an hour with no Merger record is a gap. Each panel
covers the last 1, 6 and 24 hours, against the 7 days before now (or as
much of them as the records hold) as a baseline. At the top: merges an
hour against a bar of 10, build runs per merge (target about 1.1) and
build runs per merged pull request (target about 2). Then:

1. **Why so few merges.** Each Merger tick is put in one state, and the
   time to the next tick, at most 10 minutes, counts to it; the rest is
   "Merger not ticking". The states, by precedence: `merging`,
   `culprit search`, `waiting on a batch top`, `waiting on a head
   check`, `handed back`, `approved, all held` and `nothing eligible`.
   Minutes in each, each hour.
2. **Flow.** Merges, arrivals, runs, hand-backs and re-cuts per hour,
   and the pull requests waiting now, by where they wait.
3. **Efficiency.** Build runs per merged pull request and per merge;
   pull requests per merge slot (merges over the green runs they rested
   on), by size; green batch tops on which all, part or none of the
   batch merged.
4. **Rework.** Hand-backs per merge, by cause, repeats apart; red head
   checks that were conflicts against other reds; batch tops' runs,
   green against red.
5. **Latency.** First approval to merge, median and p90. Locally, the
   first approval is when the Merger first listed the pull request; with
   `--github`, it is GitHub's first approving review.
6. **Capacity.** Slot-hours by what they went on, against slots times
   the window; the median wait for a slot and run time; how busy each
   agent was, reviewers first; run times of heads and batch runs.
7. **Policy.** Who holds right of way and since when, overdue pull
   requests, batch re-cuts and their causes, and, while
   `merge.test_ahead.trains` is a dry run, what trains would have done.

Between panels 2 and 3, the recent trains and two pictures of now,
drawn in HTML and CSS with no script (each pull request linked to
GitHub when the configuration names the repository):

* **Recent trains**, from `merger/merger.jsonl`: the last
  `--recent-trains` trains the Merger cut, newest first, one row each:
  an outcome badge, when it was cut, "N PRs in M carriages", how long
  from the cut to its last member leaving it, the last run of its top
  in `worker.log` and `verdicts.log` (result and run time), and its
  carriages, each member coloured by what became of it. How it is read:
  * a record's `trains` (a live one, not `trains_dry`) lists each
    train's pull requests. A train that is a subset of what is left of
    one before goes on as that train; any other was cut that tick, and
    so was every train when `train_events` says `main moved`. A record
    without `trains` (a red train's search) only settles members by
    its decisions;
  * a member is **merged** by a MERGE decision (`train: true` is the
    train's own merge, else on its own), the **culprit** by a
    hand-back with `suspect: true` (found red on its own by the
    search), **left** by any other hand-back or by dropping out of the
    trains without boarding another (its hold, if any, says why),
    **re-cut** by boarding a train cut later (the record's
    `train_events` say why), and **still in the train** otherwise;
  * the train is **merged** when every member merged, **partly
    merged** when some did, **red** when none did and its top's run was
    red or a culprit was found, **re-cut** otherwise, and **running**
    while it is still in the trains. A red one says whether the
    Merger's culprit search ran in its time and who the culprit was.
  In `flow.json` as `recent_trains` (`cut`, `end`, `minutes`, `prs`,
  `carriages`, `outcome`, `why`, `members` by number with `outcome`,
  `why`, `at`, `tops`, `searched`).
* **Trains now**, from `merger/ahead.json`: one row per train, the
  one merging next first, drawn as a train: an engine at the front,
  then its carriages, each a box on wheels coupled to the next, and
  the top (the commit tested for the whole train) double-bordered in
  the guard's van at the back. A carriage is a pull request and the
  stacked layers that board with it: ahead.json's `carriage` (the
  carriage's first pull request), or, without it, a pull request held
  as stacked on the one before it. Each train says "N PRs in M
  carriages". The engine says where its top's run is: standing, waiting
  for a slot (no run in `worker.log`); steaming, testing (the steam
  moves unless the reader asks for reduced motion); flag out and a
  green lamp, green (merging); a red lamp, red. A train cut for urgent
  pull requests (`urgent_cut`) says so. An `ahead.json` without trains
  is cut by its batch's and later batches' sizes, and what is left is
  shown as predicted but in no train yet. In `flow.json` as `trains`
  (`train`, `prs`, `carriages`, `top`, `state`, `since`, `urgent`) and
  `ahead_at`.
* **Stacks now**, from the last Merger record's `stacked on #N` holds:
  each stack a tree rooted at its base (the pull request on the
  default branch), largest first, with its layers, depth, and where
  the base waits (in a train, in the queue, held and why, back with
  its author, or not a candidate), since the whole stack waits there.
  Each layer has a status dot, from the last Merger record first,
  then the build check (the last run of the pull request's own head,
  or a green it posted without a run):
  * green, can merge: in a train whose top is green, in the Merger's
    queue (eligible), merged (a layer above is held as `stacked on #N,
    which merged`), or held only as `stacked on #N` with its own check
    green, so it boards when the one below does;
  * orange, testing underway: in a train testing or waiting for a
    slot, held only as stacked with its own check running or not run
    yet, queued with its own check running, or held for a check still
    to come (a hold that says `pending`, `expected`, `(absent)`,
    waits for a new batch's top, or waits for its train);
  * red, cannot merge: in a train whose top is red, handed back at the
    last tick, held for anything else (a conflict, a review after the
    approval, `Blocked-by:`, a draft, a hold label, a red check, a
    layer waiting for something of its own), its own check red, or not
    a candidate at all (not approved, or not open).
  In `flow.json` as `stacks` (`pr`, `layers`, `count`, `depth`,
  `root_where`, and each node's `status` and `why`).

In both, a pull request the Merger puts first has a mark: **!!**
urgent, carrying `labels.urgent` (with `--github`, read from GitHub;
otherwise inferred: one in ahead.json's `urgent_cut`, the queued pull
requests the Merger found urgent, that fixes nothing red); **!**
elevated, one the Merger treats as urgent without the label (a fix of
a red nightly or default branch, as above) or the holder of right of
way in the last Merger record. In `flow.json` as `priority`
(`{"<pr>": {"level", "why"}}`).

`forge-stats-watch` reads three of these figures in its own process
(`hand_backs_per_merge`, `prs_per_slot`, `reviewer_busy`).

A run ends with one line on standard output:

    forge-flow: wrote /home/agent/flow/widget (merging; 4 merges in the last hour; 1 history lines)

With `--github`, a failure to read GitHub is reported on standard error
(`forge-flow: GitHub's approvals left out: …`, `forge-flow: urgent
labels inferred, not read: …`) and the page is written without them.

## State and logs

It keeps no state directory of its own. It writes into `--out`:

| File | What it is |
| --- | --- |
| `index.html` | The page, rewritten each run. |
| `flow.json` | Every number on the page. |
| `flow-history.jsonl` | One line per whole hour, each appended once. |
| `approvals.json` | With `--github`: each merged pull request's first approval, so each is asked of GitHub once. |

From cron, its output is in `~/cron-logs/forge-flow.log`.

## Exit status

* **0**: the page was written.
* **2**: no configuration (when one is needed: without `--state-dir`,
  or with `--github`), or no records in the state directory
  (`NO-RECORDS: nothing in <dir>`).

## See also

* [designs/0045](../designs/0045-a-page-that-says-why-merging-is-slow.md).
* [forge-stats](forge-stats.md), the same records as a text report over
  any window.
* [forge-stats-watch](forge-stats-watch.md), which files an issue when a
  figure is out of line.
* [config.md, The flow page](../config.md#the-flow-page).
