# forge-merger

`forge-merger` is the Merger: the only thing that merges into the
default branch. It takes each pull request a reviewer agent has
approved, at a head the approval still covers, and with nothing said
or blocking since, and merges them one at a time. For the first in its
queue it updates the branch, waits for the build check, merges, or
hands the pull request back with a reason. A project wants it so that
nothing lands that a reviewer didn't approve and the build check
didn't pass, and so that agents never wait on a person to press merge.
It never merges on its own judgment: it merges what reviewers approved.

## Usage

    forge-merger [--dry-run] [--state-dir DIR] [--checkout DIR]
                 [--check-state DIR]

It is normally run in two ways at once, both through
[forge-cron-run](forge-cron-run.md), which keeps the cron checkout at
the default branch, holds the locks and appends to
`~/cron-logs/forge-merger.log`:

* **On webhook events**, by [forge-receiver](forge-receiver.md). The
  project's `receiver.ticks` names it for the events that can move the
  queue, such as `pull_request_review`, `status` and `push`:

      "ticks": {"pull_request_review": ["forge-merger"],
                "status": ["forge-merger"],
                "push": ["forge-merger"]}

* **From cron**, as a backstop for missed events:

      */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
          $HOME/.local/bin/forge-cron-run forge-merger

Without a receiver, run it every minute or two, and set
`FORGE_CRON_LOCK_WAIT_S` below the period so waiters don't pile up.

To watch what it would do, run it by hand with `--dry-run` against the
cron checkout. A dry run writes nothing to GitHub:

    forge-merger --dry-run --checkout ~/widget-cron

It needs `git` (2.38 or later, for `git merge-tree --write-tree`),
`gh` and `openssl` on the path.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--dry-run` | off | Read and report, and write nothing to GitHub. It writes its record to `dry-run.jsonl` instead of `merger.jsonl`, and never writes `next.json` or `ahead.json`. It reads as [`merge.read_as`](../config.md#mergeread_as) when that is set, else as the Merger's App. |
| `--state-dir DIR` | `~/.local/state/agent-forge/<project>/merger` | Where its records and state live ([Where state lives](../config.md#where-state-lives)). |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT` | The cron checkout, kept at the default branch. Its configuration is the one used, and gate tools run there. With neither, it prints `NO-REPO` and exits 2. |
| `--check-state DIR` | `~/.local/state/agent-forge/<project>/pr-check` | The build check's state directory. Read only with [`merge.queued_stall_minutes`](../config.md#mergequeued_stall_minutes) or `merge.test_ahead.batch` above 1. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`. |
| `FORGE_STATE_DIR` | Replaces `~/.local/state/agent-forge` in the state directories. |
| `XDG_STATE_HOME` | Replaces `~/.local/state` when `FORGE_STATE_DIR` is not set. |
| `FORGE_ROUTER_RELAY` | Overrides `relay.url` for stall messages ([`merge.stall_alert`](../config.md#mergestall_alert)). |
| `FORGE_GITHUB_API` | The GitHub API base used to mint App tokens (default `https://api.github.com`). |

The configuration is always the checkout's own
`.agent-forge/config.json`; `FORGE_CONFIG` is not read.

## Configuration

* **Keys**: `repo`, `default_branch`, `owner`, `agents` (each agent of
  `kind` `reviewer` is an approver), `machinery` and `key_dir`
  ([Keys](../config.md#keys), [An agent](../config.md#an-agent)). The
  App that acts, `merge.merger` (default `Merger`), is usually an entry
  in `machinery`.
* **`merge`**: every key in the `merge` row of
  [Keys](../config.md#keys), each with its own section:
  [`stall_alert`](../config.md#mergestall_alert),
  [`test_ahead`](../config.md#mergetest_ahead),
  [`hold_conflicting`](../config.md#mergehold_conflicting),
  [`yield_after_hand_back`](../config.md#mergeyield_after_hand_back),
  [`early_conflicts`](../config.md#mergeearly_conflicts),
  [`code_owners`](../config.md#mergecode_owners),
  [`age_first`](../config.md#mergeage_first),
  [`right_of_way`](../config.md#mergeright_of_way),
  [`stacks`](../config.md#mergestacks),
  [`resolve`](../config.md#mergeresolve),
  [`resolve.union`](../config.md#mergeresolveunion),
  [`hold_labels`](../config.md#mergehold_labels),
  [`blocked_by`](../config.md#mergeblocked_by),
  [`said_since`](../config.md#mergesaid_since),
  [`update_once`](../config.md#mergeupdate_once),
  [`hand_back_once_per_head`](../config.md#mergehand_back_once_per_head),
  [`own_head_green`](../config.md#mergeown_head_green),
  [`ci_never_started`](../config.md#mergeci_never_started),
  [`queued_stall_minutes`](../config.md#mergequeued_stall_minutes) and
  [`read_as`](../config.md#mergeread_as). The basics are `merger`,
  `ci_workflow` (default `ci`), `gate` (default
  `[["forge-commit-identity", "{base}..{head}"]]`) and `stall_minutes`
  (default 30).
* **`checks`**: `context` (the status it requires, default
  `vm/build-and-test`), `poster` (whose statuses count, default the
  Merger) and each `checks.instances.<name>.gate` with that block's
  `context`, `poster` and `pull_requests`
  ([`checks`](../config.md#checks),
  [`checks.instances`](../config.md#checksinstances)).
* **`labels`**: `urgent`, `rerun` and `waiting_on_owner`
  ([Keys](../config.md#keys)).
* **`relay`**: `url` and `token_file`, only with `merge.stall_alert`.
* **`nightly`**: through `forge-nightly`'s state, which pull requests
  fix a red nightly.

## What it does

### Which pull requests are its

A pull request is a candidate when all of these hold:

* it is not a draft, and is based on the default branch (or, with
  [`merge.stacks`](../config.md#mergestacks), on another open pull
  request's head branch: it is then held as `stacked on #Q`);
* it carries no [`merge.hold_labels`](../config.md#mergehold_labels)
  label;
* it has an `APPROVED` review by the App of an agent of `kind`
  `reviewer` that did not open it. Any reviewer's approval counts,
  whatever the area;
* **nobody has spoken since the approval**: no comment or review after
  it, except one with `Not-a-review` in its text, one by the Merger's
  own App, one by the author, or another approval.
  [`merge.said_since`](../config.md#mergesaid_since) can count the
  author's remarks and other approvals too. Anything else makes it the
  reviewer's again, and the Merger says so once per approval and
  remark;
* no request for changes stands: a reviewer's newest verdict that is
  `CHANGES_REQUESTED` holds it until that reviewer reviews again or it
  is dismissed;
* nothing a `Blocked-by: #N` line names is still open
  ([`merge.blocked_by`](../config.md#mergeblocked_by) says where those
  lines are read). A `Blocked-by:` line that doesn't parse holds it;
* no `Defer-until:` line (the newest anywhere on it) names a time
  still to come, or a value that isn't a time;
* **the approval covers the head.** Call the approved commit A and the
  head H. Either H is A, or every commit in H but not in A or the
  default branch is a merge with one parent on the default branch,
  whose tree is exactly `git merge-tree --write-tree` of its parents
  (or, with [`merge.resolve.union`](../config.md#mergeresolveunion),
  the resolution the Merger makes of them). GitHub carries approvals
  across any push; this check stops an unreviewed commit riding on
  one. An author's clean merge of the default branch passes, and so
  does the merge [forge-resolve](forge-resolve.md) makes;
* with [`merge.code_owners`](../config.md#mergecode_owners), an owner
  of each CODEOWNERS path it changes wrote it or approved it;
* with [`merge.own_head_green`](../config.md#mergeown_head_green) (on
  by default), `checks.context` is green on the approved commit or the
  head;
* with an instance's `gate`, that instance's context is `success`
  ([`checks.instances`](../config.md#checksinstances)).

An approved pull request that fails one of these is **held**: the
tick's record lists it under `held` with the reason, and the queue
moves past it. A hold posts nothing, except where noted.

### The order of the queue

1. With [`merge.test_ahead`](../config.md#mergetest_ahead), the pull
   request a run is in flight for (the one the last tick updated or
   waited on) keeps first place, then those already predicted, in the
   order predicted. With `test_ahead.green_batch_first`, a batch green
   on the default branch's tip goes before it while its own run hasn't
   finished (`BATCH-FIRST`).
2. Urgent ones: those carrying `labels.urgent`, those that fix a red
   nightly while `forge-nightly`'s issue is open, and those that fix a
   red default branch while `forge-pr-check`'s `main_red_issue` is
   open. Oldest first. They go behind only the one in flight and a
   green batch still merging.
3. Those another open pull request is blocked by.
4. The rest, oldest opened first.

Several settings then move pull requests within that order:
[`yield_after_hand_back`](../config.md#mergeyield_after_hand_back)
moves back one handed back lately,
[`age_first`](../config.md#mergeage_first) moves forward one that has
waited long, [`right_of_way`](../config.md#mergeright_of_way) puts
first one overtaken again and again and holds what conflicts with it,
and [`stacks`](../config.md#mergestacks) puts a retargeted layer first.
With `test_ahead.fixed_order` (on by default), batches whose top's run
has started keep their order against all of these.

### One decision per tick

For the first candidate it can act on, a tick decides one action:

| Action | When |
| --- | --- |
| `UPDATE` | It is behind the default branch, and merging that in is clean. GitHub's `update-branch` is asked, pinned to the head judged, and a comment says the approval still covers it. |
| `UNION` | Merging the default branch conflicts only where both sides added lines, in files [`merge.resolve.union`](../config.md#mergeresolveunion) names. The Merger commits that merge as its App, pushes it with a lease, and comments with the lines kept. |
| `RESOLVE` | Every conflicting path is in a [`merge.resolve`](../config.md#mergeresolve) rule: the Merger regenerates and pushes. |
| `RETARGET`, `RESTACK`, `STACK-UPDATE` | A stack's layer is moved onto the default branch, or the layer below's head is merged into it ([`merge.stacks`](../config.md#mergestacks)). |
| `WAIT` | `checks.context` has not reported on the head, or a run of `merge.ci_workflow` on it hasn't finished. |
| `MERGE` | Every check of the gate passed: it squash-merges, pinned to the head, with GitHub's default message. |
| `HAND-BACK` | A check failed. It goes to the author, or to the approving reviewer when the approval no longer covers the head. |
| `SKIP` | The question can't be answered this tick (a fetch failed, the head moved while reading). |
| `RESYNC` | GitHub lost the head (below); the next tick judges the new one. |

**The gate**, on a head that contains the default branch's tip:

* the newest status `checks.context` on exactly that SHA is `success`,
  posted by `checks.poster`'s App. Statuses from anyone else don't
  count;
* every run of the Actions workflow `merge.ci_workflow` on it
  succeeded (`""`: no workflow);
* each gating instance's context is `success`;
* every `merge.gate` tool exits 0. Each is an argument list with
  `{pr}`, `{base}` and `{head}` filled in, and `{below}` for a stack's
  merged layer below. A `forge-` tool runs from the installation,
  anything else from the cron checkout, so a pull request can't change
  the rule it is judged by. A tool that exits 3 refuses only for now:
  it is asked again 5 minutes later.

A tick makes at most one update or merge. A run whose tick merged, or
retargeted a stack's layer, ticks again at once, up to 10 ticks, so the
next pull request starts without waiting for the next event. Before the
one it acts on, it hands back each pull request ahead of it that it
can't take.

### Hand-backs

A hand-back is a comment, signed `— Merger` (the App's name). To the
author it is a plain comment; for a conflict it says what to do and
lists the author's other open pull requests and where each stands. To
the approving reviewer it starts `Not-a-review`, since it asks for a
review rather than giving one, and lists the paths whose content the
reviewer hasn't seen together with the default branch's, so the
re-review can start there.

It is said once per approval and head, whatever the reason
([`merge.hand_back_once_per_head`](../config.md#mergehand_back_once_per_head));
`handed-back.json` remembers what was said. Common reasons:

* a conflict with the default branch (`it conflicts with main, so it
  can't be updated`). `git merge-tree` finds it before any write, and
  a conflict is never retried: retrying would stall everything behind
  it;
* a red build check or CI run on the head;
* a gate tool's refusal;
* the approval doesn't cover the head (to the reviewer);
* a head GitHub keeps refusing (below), or CI that never finishes;
* with [`merge.ci_never_started`](../config.md#mergeci_never_started),
  CI that never started on the head.

With [`merge.update_once`](../config.md#mergeupdate_once), a pull
request handed back under an approval isn't updated again until it is
approved again. With
[`merge.early_conflicts`](../config.md#mergeearly_conflicts), every
candidate behind the front that conflicts with the default branch is
handed back at once, not when it reaches the front.
[`merge.hold_conflicting`](../config.md#mergehold_conflicting) holds,
rather than hands back, one that conflicts with one ahead of it.

### GitHub's mistakes

* **A lost head.** GitHub sometimes moves a branch without moving the
  pull request's head, then refuses every write pinned to either
  ("expected head sha didn't match"). The Merger then sets the pull
  request's base to the base it already has, once per pull request and
  head, which makes GitHub read the head again, and logs `RESYNC #N`.
  If that doesn't help, after `stall_minutes` it hands the pull
  request back and moves on. That hand-back never counts against the
  author.
* **A CI run reported unfinished.** For a run GitHub calls `queued` or
  `in_progress`, the Merger reads its jobs. If all have completed, it
  judges the run by them (`CI-STALE`). A run still unfinished
  `stall_minutes` after it started is re-run once per head
  (`CI-RERUN`); still unfinished `stall_minutes` after that, the pull
  request is handed back (`CI-STUCK`) and the queue moves past it.
* **Issues left open.** A couple of minutes after a merge, a live tick
  closes each issue the pull request's `closingIssuesReferences` names
  that GitHub left open, with a comment, and logs `CLOSED-ISSUE`.

### Stalls

A head it has waited on for `stall_minutes` (default 30) with no final
`checks.context` status, or an unfinished CI run, is `STALLED`: the
tick prints a `STALLED:` line and exits 2. It is not handed back, since
its author can't fix a stuck check, and it stays first. A write GitHub
goes on refusing for as long is `STALLED` too, quoting the refusal.
With [`merge.queued_stall_minutes`](../config.md#mergequeued_stall_minutes),
a head the build check still has queued stalls after that time
instead. A batch member's wait is timed from when its batch's top
started, or a run of the top's tree at another commit did. A wait on
checks stalls only after
[`merge.stall_run_factor`](../config.md#mergestall_run_factor) times
the build check's recent median run, when that is longer. With [`merge.stall_alert`](../config.md#mergestall_alert), a
stall also opens an issue and messages agents through the relay
(`STALL-ISSUE`, `STALL-TOLD`, `STALL-TO-OWNER`, `STALL-ENDED`).

### Test ahead, batches and trains

Under branch protection that requires heads to be up to date, merging
is serial: update, wait for the build check, merge, then the next. With
[`merge.test_ahead`](../config.md#mergetest_ahead), while it waits on
the first pull request, a live tick writes the commit each of the next
`depth` will have once those ahead have merged, as local commits under
`refs/agent-forge-merger/ahead/<k>` in the cron checkout, listed in
`ahead.json`. They have a fixed author and date, so the same inputs give
the same SHA. [forge-pr-check](forge-pr-check.md) tests them in free
slots, from the same object store, so both tools must use the same
checkout. When GitHub's update makes a tree already tested green, the
build check posts green at once and the Merger merges on its next
tick. A conflict with those ahead leaves a pull request out of the
predictions. Before predicting, each pull request's approval and gate
tools are checked once per head (`CHECKED-AHEAD`, `checked.json`), so
one that fails is handed back before its batch.

* **Batches** (`batch` above 1,
  [designs/0032](../designs/0032-merge-in-batches.md)): only the
  batch's top is run; its green covers each member, which the Merger
  then merges one a tick. `BATCH #a..#b: top <sha> green, merging N`.
  A red top makes its members suspects (`suspects.json`): their
  prefixes are tested until one is red on its own (`SUSPECTS ...`),
  which is handed back (`SUSPECT-RED`, `red-suspects.json`) and
  released by a new head or a later green run of the same head
  (`SUSPECT-CLEARED`). Every suspect merging green logs
  `BATCH-FLAKE`. A red that is only the default branch's own logs
  `BATCH-BASE-RED` and names nobody. Urgent pull requests are batched
  apart.
* **Several batches** (`batches` above 1,
  [designs/0035](../designs/0035-batches-take-stacks-run-side-by-side-and-size-themselves.md)):
  later batches are listed as `later` and their tops run in spare
  slots. With `unstable_after_recuts`, too many re-cuts in an hour cut
  the number back (`UNSTABLE`, `SETTLED`).
* **Trains** (`trains`,
  [designs/0047](../designs/0047-fixed-trains-of-pull-requests-that-touch-different-files.md)):
  each batch is cut once from pull requests that touch different
  files, and then fixed (`TRAIN-CUT`, `TRAIN-LEFT`, `TRAIN-RECUT`).
  `"dry-run"` only logs the trains it would cut (`TRAIN-DRY`,
  `trains-dry.json`).

The pull requests that fix a red default branch, and whether a red is
only the default branch's, come from the build check's state. That is
read only with `merge.queued_stall_minutes` or `test_ahead.batch`
above 1.

### Stacks

With [`merge.stacks`](../config.md#mergestacks)
([designs/0025](../designs/0025-merge-a-stack-as-a-unit.md)), a pull
request based on another's branch waits as `stacked on #Q`. When the
Merger merges Q, it records the layers above (`stacks.json`), the next
tick retargets the first eligible one to the default branch (`STACK
#N: retargeted to main after #Q merged`), and that layer goes first.
It merges the layer below's final head into a layer when needed
(`RESTACK`, `STACK-UPDATE`), once per approval and head, and says no
new review is needed. A layer whose base was merged by hand on GitHub
is found too (`merged-elsewhere.json`). `wait_hours` lets a layer not
yet ready stay in its stack;
`predict_restacked` predicts a layer as it will be restacked.

### What each tick prints and records

Each tick ends with one line:

    TICK 4 eligible, 2 approved but held; #12 UPDATE (done)

(`DRY-RUN` instead of `TICK` under `--dry-run`; `nothing to do` when
there are no decisions), plus a `STALLED:` line when stalled. It
appends one JSON record to `merger.jsonl`: the default branch's tip,
`held` with reasons, the `queue`, the `decisions` (each with `pr`,
`action`, `to`, `reason` and `result`), `urgent` (every open pull
request carrying `labels.urgent`, queued or held) and `elevated` (those
urgent only as a red nightly's or default branch's fix), and, as
settings apply,
`yielded`, `overdue`, `right_of_way`, `stack`, `holds`, `fixed`,
`later`, `trains` and `stalled`. Every live tick writes `next.json`
(`{"at", "pr"}`, plus `action` with `test_ahead`), naming the pull
request it stopped at, which `forge-pr-check` reads for
[`checks.merger_first`](../config.md#checks).

Other lines worth knowing: `BUSY` (another tick holds the lock),
`HAND-BACK-CLEARED` (a hand-back forgotten after a green rerun, or a
head back at the approved commit), `UNION`, `STACK`, `NO-TOKEN`,
`NO-CONFIG`, `FAILED`.

### What it writes to GitHub

Live ticks only, as the `merge.merger` App: branch updates, squash
merges, pushes of its own merge commits (`UNION`, `RESOLVE`,
`STACK-UPDATE`, with a lease on the head it judged), base changes
(`RETARGET`, `RESYNC`), CI re-runs, comments (hand-backs, holds,
updates), issue closes, and the stall issue and its label. It never
approves and never posts the build check's status.

## State and logs

In the state directory (`--state-dir`, default
`~/.local/state/agent-forge/<project>/merger`):

| File | What it holds |
| --- | --- |
| `lock` | Held by the running tick; a second tick prints `BUSY` and exits 0. |
| `merger.jsonl`, `dry-run.jsonl` | One record per tick, live and dry run. |
| `next.json` | The pull request the last live tick stopped at, and as `blocked` the approved ones held until another merges (`Blocked-by:`, stacked), for the build check's `approved_first` order. |
| `ahead.json` | The predictions, batches and trains (`test_ahead`). |
| `handed-back.json` | What was handed back, per approval and head. |
| `held-said.json`, `held-conflicting.json` | Which holds were said. |
| `waiting.json` | Since when it has waited on which head. |
| `greens.json` | Commits found green (`own_head_green`). |
| `checked.json`, `suspects.json`, `red-suspects.json`, `trains-dry.json` | Batch checks, suspects and dry-run trains. |
| `stacks.json`, `merged-elsewhere.json`, `restacked.json`, `stack-updated.json` | Stacks. |
| `waiting-since.json`, `right-of-way.json` | `age_first` and `right_of_way` (with a `dry-run-` prefix for a dry run). |
| `resolved.json`, `resolve-checked.json`, `unioned.json` | `merge.resolve` and its `union`. |
| `ci-reruns.json`, `resynced.json`, `closing.json` | CI re-runs, lost-head nudges, issues to close. |
| `stall-alert.json` | The open stall issue. |

It also reads the build check's `state.json` (`--check-state`) and
`forge-nightly`'s state. Its output goes to the log `forge-cron-run`
keeps, `~/cron-logs/forge-merger.log` by default.

## Exit status

* 0: the tick ran, whatever GitHub answered its writes, or another
  tick held the lock.
* 2: it could not ask (no checkout, no configuration, no token, GitHub
  or git failed), or the head it waits on is `STALLED`.

## See also

* [`merge`](../config.md#keys) and its sections in config.md.
* [forge-pr-check](forge-pr-check.md), the build check it requires.
* [forge-resolve](forge-resolve.md), which makes an author the merge the
  Merger would make.
* [forge-cron-run](forge-cron-run.md) and
  [forge-receiver](forge-receiver.md), which run it.
* [merger-sim.md](../merger-sim.md), the simulation that runs it
  tick after tick against real git and a model of GitHub and the
  build check, and the rules it holds it to.
* [designs/0001](../designs/0001-fewer-wasted-rounds-in-the-merge-queue.md),
  [designs/0026](../designs/0026-merger-settings-for-a-stricter-project.md)
  and [designs/0044](../designs/0044-a-quieter-merger-and-batches-that-keep-their-order.md).
