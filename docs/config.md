# Configuration

A project describes itself to agent-forge in one file,
`.agent-forge/config.json`, in its own repository. It is the only
project-specific input: the tools read everything else from GitHub and
git. [`examples/config.json`](../examples/config.json) is a complete
example, and `forge-config check` checks a file.

## Finding it

A tool reads, in order:

1. `$FORGE_CONFIG`, the file itself;
2. `.agent-forge/config.json` under `$FORGE_PROJECT_ROOT`;
3. `.agent-forge/config.json` at the top of the git checkout around the
   working directory.

So one installed copy of agent-forge serves every project on a host:
a scheduled job runs from that project's checkout.

The tools that say who an agent is (`forge-app-token`,
`forge-git-credential`, `forge-worktree-identity`, `forge-gh-signed`)
and `forge-session-start` read `.agent-forge/config.json` under
`$FORGE_CRON_CHECKOUT` before step 2, when it is set. Set it in a
host's agent sessions (the `env` of Claude Code's settings), and an
agent's branch can't change the roster its own App token, push
credential and signature are judged by: they come from the checkout
kept at the default branch. The documentation checks still read the
branch's own file.

## Checking

The whole file is checked when it is loaded, and every problem is
reported at once. A key agent-forge doesn't know is a problem, not
something to ignore, since it is usually a misspelling. Any key that
starts with `_` is a comment, at any level.

## Keys

| Key | Required | What it is |
| --- | --- | --- |
| `project` | yes | A short name, lower case letters, digits and hyphens. It names the state directory. |
| `repo` | yes | `owner/name` on GitHub. |
| `owner` | yes | The human's GitHub login: who approves designs and decisions. |
| `agents` | yes | Each agent, by name; see below. |
| `default_branch` | no | Default `main`. |
| `host` | no | The host the build check runs on, as statuses name it. |
| `key_dir` | no | Where the Apps' private keys are, `~` allowed. |
| `naming` | no | `app_slug_suffix` and `session_prefix`; see below. |
| `machinery` | no | Apps that act for scheduled tools rather than agents, such as the Merger's; each entry as an agent's App fields. |
| `identity` | no | The host's unconfigured git identity, which no commit may carry: `unattributed_domain` (an email domain such as `@host.example.org`; default `$FORGE_UNATTRIBUTED_DOMAIN`, else the host's own fully qualified name, so a configuration need not name its hosts) and `unattributed_name`. |
| `merge` | no | How `forge-merger` merges: `merger`, the machinery App that acts (default `Merger`); `ci_workflow`, the Actions workflow every head must pass (default `ci`; `""` for none); `gate`, tools that must exit 0 before a merge (one that exits 3 refuses only for now, as `forge-closing-keywords` does while GitHub's `closingIssuesReferences` lags a body edit: its hand-back is said once and the tool is asked again 5 minutes later), each an argument list with `{pr}`, `{base}` and `{head}` filled in and an argument that is exactly `{below}` replaced by the number of a stack's merged layer below (left out when there is none, so `["forge-closing-keywords", "--pr", "{pr}", "--below", "{below}"]` leaves a restacked layer's lower commits out; see `bin/forge-closing-keywords`), a `forge-` name run from the installation and anything else from the cron checkout (default `[["forge-commit-identity", "{base}..{head}"]]`); `stall_minutes`, how long a head may wait on its checks, or have a write refused, before the tick reports it stalled (default 30; a head GitHub goes on refusing because the head it expected didn't match is then handed back to its author and the queue moves past it; a run of `ci_workflow` GitHub still has unfinished, its jobs too, this long after it started is re-run once per head, and if it still hasn't finished this long after that, the pull request is handed back and the queue moves past it: `bin/forge-merger`); `stall_alert`, whom a stall is told to (default none: a stall is only printed; see below); `test_ahead`, whether the next pull requests are tested ahead (default none: they aren't; see below); `hold_conflicting`, whether a pull request that conflicts with one ahead of it in the queue waits for it rather than being handed back after every merge (default `false`; see below); `yield_after_hand_back`, how many places a pull request handed back lately lets others go ahead of it, per hand-back (default none: nobody yields; see below), `yield_cap`, the most hand-backs counted (default 3), and `yield_ignores_conflicts`, whether a conflict with `main` is left out of that count (default `false`); `right_of_way`, whether a pull request overtaken again and again holds back what conflicts with it (default none: nobody has it; see below); `age_first`, whether a pull request that has waited long goes first (default none; see below); `code_owners`, whether a pull request that changes a path CODEOWNERS assigns waits for an owner's approval (default `false`; see below); `early_conflicts`, whether a candidate behind the front that conflicts with the default branch is handed back at once (default `false`; see below); `stacks`, whether a stack of pull requests is merged as a unit (default none: a pull request not based on the default branch is held; see below). Designs/0026 adds `resolve`, `hold_labels`, `blocked_by`, `said_since`, `update_once`, `ci_never_started`, `queued_stall_minutes` and `read_as`, each off by default (see below). The required status is `checks.context`, counted only when `checks.poster`'s App (default the merger) posted it. |
| `host_settings` | no | What every agent host carries, installed by `forge-host-settings`: `files`, each `{"from": path in the project, "to": path under the home directory, "mode": "0644", "json": true when compared as JSON}`; `host_keys`, keys a JSON file's host may set for itself; and `crontabs`, each host's crontab by its short name, `{"<host>": path in the project}`. A host installs only its own entry, and a host with none keeps its crontab. Run from a host's crontab, `forge-host-settings --apply` installs whatever has changed on the default branch, so keep these files code-owned. |
| `memory` | no | Where the agents' memories are shared between hosts, for `forge-memory-sync`, `forge-memory-check` and `forge-memory-trim` ([memory-sync.md](memory-sync.md)): `dir`, the memory directory sessions load (`~/.claude/projects/<slug>/memory`), and `remote`, the repository that carries it, both required; `git_dir`, its git directory, outside it (default `~/.<project>-memory.git`); `seed_dir`, where `--seed` keeps a host's file it replaced (default `~/.<project>-memory-seed`); `ssh_key`, a key used for `remote` alone, such as a deploy key (default none: git's own setup); `branch` (default `main`); `labels`, the labels of the issue a stopped sync is reported on (default none); `limit`, the bytes of `MEMORY.md` the harness loads (default 24985), and `long_line`, the bytes past which one index line is reported (default 200). Paths may start `~`. On a host, `FORGE_MEMORY_DIR`, `FORGE_MEMORY_REMOTE`, `FORGE_MEMORY_GIT_DIR`, `FORGE_MEMORY_SEED_DIR` and `FORGE_MEMORY_SSH_KEY` override them, and `FORGE_MEMORY_HOST` names the host (default its short name). |
| `quiet` | no | Quiet holds on a shared host, for `forge-quiet` and what honours them (agent_forge/quiet.py): `dir`, the directory holds are kept in, one file per holder, `~` allowed (required in the section; `$FORGE_QUIET_DIR` overrides it for `forge-quiet` and `forge-pr-check`, for a host where it can't exist, such as a Mac's), and `max_minutes`, the longest hold (default 120); `settle`, a command (an argument list) `forge-quiet hold` runs once the hold is written, again every 30 seconds until it exits 0, for heavy work that started before the hold and that the hold can't stop, such as a deploy already building (default none; a `forge-` name runs from the installation, a relative path from the configuration's checkout, with `FORGE_QUIET_HOLDER`, `FORGE_QUIET_MINUTES`, `FORGE_QUIET_UNTIL` and `FORGE_QUIET_DIR` set), and `settle_minutes`, how long it is given (default 30). Exit 1 means busy; any other failure is an error, and three in a row end the wait. The hold is renewed while it settles and taken again for its full minutes once settled, so settling doesn't spend it. It is read on whichever host a tool runs, so a host without the directory is never held. While a hold is live there, `forge-pr-check` starts no run. |
| `queue` | no | Queues of slow work, for `forge-queue` ([designs/0023](designs/0023-a-queue-that-tells-you-when-your-job-is-done.md)); inert until present, and nothing can be submitted without it: `queues`, the queues by name (lower case letters, digits and hyphens), each with `slots`, how many of its jobs run at once (default 1), `nice` (0 to 19, default 10), `timeout_minutes`, after which a job is stopped (default 120), and `keep_worktree`, whether the frozen worktree a job ran in is kept after it ends (default `false`); `state_dir`, where tickets, logs and frozen worktrees live (default `~/.local/state/agent-forge/queue`, `~` allowed); and `retain_days`, after which an ended ticket and its log are removed (default 7). A job's agent is told it ended through the `relay` section, at her session names from `agents` and `twins`, and an undelivered message is tried again every `relay.remind_seconds`. |
| `stats_watch` | no | What `forge-stats-watch` watches of `forge-stats`' figures, and whom it tells ([designs/0024](designs/0024-a-watch-on-how-fast-work-lands.md)); inert until present: `owner`, the agent its issues are for, one of `agents` (required); `window_hours`, the window judged (default 6), against the `baseline_days` before it (default 7); `figures`, the figures watched, each with its rules, and a figure left out is not watched: `check_run` (the build check's run time, median), `check_wait` (the wait for a slot, p90), `pr_turnaround` (a pull-request head's wait and run, median) and `merge_time` (joined the queue to merged, median), each out of line over `limit` minutes or over `ratio` times the baseline; `merge_rate` (merges an hour), under `limit`, or under the baseline over `ratio`, judged only when approved pull requests waited; `slots_busy`, over `limit` percent; `main_red`, `{}`, any red build check of the default branch; and `nightly_red_hours`, the hours since the last green nightly ended (`forge-nightly`), over `limit` hours, or any above 0 without one. `limit` is a number above 0 and `ratio` one above 1, each optional; any figure may also name its own `owner`, one of `agents`, whose its issue is instead of the watch's `owner`. Also `min_samples`, below which a figure (or a baseline, for `ratio`) is not judged (default 5; for `merge_rate`, the approved pull requests that waited); `label`, the label of its issues (default `stats-watch`), each also labelled `<labels.owner_prefix><owner>`; `remind_hours`, how often an open issue is commented on while its figure stays out (default 6); and `as`, the agent or machinery App it files as (default `merge.merger`, else `Merger`). An issue closes once its figure has been in line for a whole window. |
| `nightly` | no | What `forge-nightly` watches: a nightly workflow whose red run is treated as a red main ([designs/0028](designs/0028-a-red-nightly-is-a-red-main.md)); inert until present: `workflow`, the workflow's file name (such as `ci.yaml`, read for a `first` input), and `job`, the job that decides it (a matrix's `job (...)` too), both required; `owners`, a failed step's owner, by its name or a glob over names, each one of `agents`, with any other step `router.owners.triage`'s, which is required; `labels`, the issue's labels besides `<labels.owner_prefix><owner>` for each owner of a failed step (default none); `retry_hours`, how long after a red run ended it is run again when no fix has merged (default 3); `escalate_hours`, one or two rising whole numbers of hours after the issue opened at which every developer agent, then the owner, is messaged (default `[24, 48]`); `tell_owner`, where the relay reaches the owner, a name it resolves or a JID (default `owner`); and `as`, the agent or machinery App it files and dispatches as, which needs Actions write (default `merge.merger`, else `Merger`). While its issue is open, the pull requests that close it are treated as if they carried `labels.urgent`. |
| `receiver` | no | How `forge-receiver` takes GitHub's webhook deliveries: `address` and `port` it listens on (default `127.0.0.1`, 8789), `path` (default `/hook`), `secret_file`, the webhook's secret, mode 0600 (default `~/.config/agent-forge/<project>/receiver-secret`), and `max_body`, the largest body in bytes (default 1048576); `ticks`, which tools each event runs, by GitHub event name (`issues`, `issue_comment`, `pull_request`, `pull_request_review`, `status`, `push`), each tool a name or an argument list, a `forge-` name run from the installation and anything else from the cron checkout (default none; `bin/forge-receiver` says when each event counts); and `route`, the router's argument list with `{items}` filled in as `N[,N]`, run for the items events name (default none: nothing is routed). |
| `docs` | no | The project's documentation, as the tools that read it see it; see below. |
| `checks` | no | The build check, `forge-pr-check`; see below. |
| `labels` | no | The labels tools read: `owner_prefix` (default `Owner:`; `Owner:<Name>` says whose an item is), `status_prefix` (default `Status:`), `waiting_on_owner` (default `Status:waiting-owner`: the owner's turn), `waiting_external` (default `Status:waiting-external`: nobody's) and `urgent` (default none: off), a label that puts a pull request first: its waiting head is checked before every other pull request's (before `first_authors` and `max_wait_minutes`; a head that failed to prepare still goes behind, and the default branch's traced run still goes first when due), and the Merger takes it before every other candidate but the one a run is in flight for and, with `merge.test_ahead`, those already predicted. Several urgent ones go oldest first. A label anyone with triage access can set, so it is for a person to use sparingly, not agents. `rerun` (default none: off), a label that runs a red head once more ([designs/0022](designs/0022-run-a-red-head-again.md)): on a posting tick, `forge-pr-check` moves the head's red verdict into `reruns` in its state (with who put the label on, from the pull request's events, and when), queues the head as if new, takes the label off, logs `RERUN #<pr> <sha>: was red(<steps>); queued by <login>` and posts pending. The second verdict stands and its description says `(run again after red(<steps>))`. On a head already run again, the label comes off with a comment saying so; on a green, pending or unrun head it just comes off. On a head given up on after `MAX_ERRORS` failures to prepare it or host faults, it clears those counts so the head is tried again in its place, logs `RERUN #<pr> <sha>: given up after <why>; queued again by <login>` and posts pending; that doesn't use up the head's one run again. Anyone with triage access can set it; once per head bounds what it costs. The poster's App needs `issues: write`. The default branch's red head, which has no pull request, is run again the same way, once, by the label on the open `checks.main_red_issue` issue; the label comes off the issue. An instance (`checks.instances`) ignores it. |
| `relay` | no | Where `forge-router` posts wakes, `forge-merger` its stall messages (`merge.stall_alert`) and `forge-queue` its end-of-job messages: `url`, the xmpp-webhook-relay's base (default `http://127.0.0.1:8788`), `token_file`, its environment file holding `WEBHOOK_TOKEN` (default `~/.config/xmpp-webhook-relay/token`), `remind_seconds`, how long before a turn is woken again (default 7200), and `room`, the room the agents share. |
| `router` | no | How `forge-router` decides whose turn an item is: `rules`, the rules module (default `"default"`, the rules in `agent_forge/rules_default.py`), or a Python file in the project's own repository, by its path from the root, read from the cron checkout so it is reviewed there. docs/router.md is what a rules module provides. `idle_offer` turns on the default rules' idle offer, `merge_notice` their merge notice, `stuck` their rules for stuck pull requests, `owners` their reading of whose an issue is, and `unreviewed` their issue for paths no reviewer covers (all below). `forget_minutes`, the engine's, is how long a turn no run decides is kept before it is forgotten (default 0: at once; below). `quiet_labels` lists labels that make an item nobody's turn under every default rule (below). Every key is checked. |
| `closing_keywords` | no | How `forge-closing-keywords` words a refusal: `enforced_by`, what refuses a pull request it fails (default `the Merger`), and `rerun`, how to run it again once GitHub's `closingIssuesReferences` catches up with a body edit (default `run the check again (the Merger does, within a few minutes)`). A project that runs it in CI names its workflow and `gh run rerun --failed <run>`. The issues it judges are `repo`'s, and its default range starts at `default_branch`. The Merger runs it as a gate: `["forge-closing-keywords", "--pr", "{pr}", "--include-subject"]`. |

### An agent

The key is the agent's name: letters and digits, starting with a
letter.

| Field | What it is |
| --- | --- |
| `app_id`, `installation_id` | The agent's GitHub App and its installation on `repo`. `null` while the App doesn't exist yet: no tool mints a token for it. |
| `slug` | The App's slug, where it isn't the name followed by `naming.app_slug_suffix`, lower case. |
| `key` | The private key's file in `key_dir`, where it isn't `<name>.pem`. |
| `session_name` | The name the agent's session is woken by, where it isn't `naming.session_prefix` followed by the name. |
| `twins` | More sessions that work as the agent, by session name: twins sharing her App, such as sessions on a second host or a second account. Wakes and the Merger's messages go to each of them as well as to her, and are delivered when any one takes them. An idle offer goes out when any of them is idle. No session name may belong to two agents, since the relay only wakes a name one session holds. |
| `away` | Another agent who stands in while she is away: every turn of hers (as owner, author, reviewer, triager or idle-report recipient) wakes the stand-in's sessions instead, saying whose it is, and so do the Merger's messages. A reviewer who is away is left out of the choice of reviewer and her stand-in, who must be a reviewer, reviews her globs too. She is never offered idle work. The stand-in must be here herself (not `away`). Her sessions stay hers in statistics. Use it to move a whole agent's work for a while, such as when one account or host is out of action; removing it hands the work back. Labels and authorship don't change, so the stand-in's own comments don't count as hers: for a long absence, relabel the items. |
| `kind` | `developer` (the default), `reviewer` or `collaborator`. A reviewer's approval is what the Merger merges on. A collaborator is a developer who works on the project only when given something, such as an agent whose main work is elsewhere: she is offered her own `Owner:` issues when idle (rule 15) like any developer, but never reported to `idle_offer.report_to` for having nothing to do. |
| `reviews` | A reviewer's path globs (`fnmatch`, so `*` crosses `/`): which pull requests are theirs to review. Reviewers with the same globs share them: each pull request goes to one of them by its number, so a second reviewer for an area halves the first's load. |
| `areas` | What is hers, for a project's own rules module to offer her work by area: `{"paths": [globs], "labels": [names]}`, each a list of non-empty strings, either left out. An item is in her area when its title or body names one of her paths, or it has one of her labels. A module reads it as `roster.areas(name)` (None without it). The default rules don't use it, and it doesn't choose a reviewer: `reviews` does. |
| `offer` | `false`: she is never offered idle work. The default rules' rule 15 skips her and rule 16 never reports her idle, as for a reviewer. A module reads it as `roster.offered(name)`. Default `true`. |

### `docs`

| Key | Default | What it is |
| --- | --- | --- |
| `paths` | `["*.md", "docs"]` | What `forge-doc-format`, `forge-doc-width` and `forge-doc-links` check when given no paths: globs relative to the checkout's top (`**` crosses directories), each a Markdown file or a directory searched for `*.md`. A glob that matches nothing is skipped, but matching no Markdown at all is an error. |
| `width` | `80` | The widest a prose line may be, for `forge-doc-width`. |
| `roles` | `docs/roles.md` | The roles file `forge-session-start` takes each agent's brief from. |
| `designs_dir` | `designs` | Where designs are, for `forge-approval-scope`. |

Only these keys are checked; the section may hold others, for tools
not here yet.

### What every step gets

Besides the host's environment (less `GH_TOKEN`, `GITHUB_TOKEN`,
every `FORGE_` setting and whatever `drop_env` names) and its build
caches, each step gets:

| Variable | What it is |
| --- | --- |
| `CHECK_RESULTS` | An empty directory of the step's own, for results such as per-shard timings: `results/<head>/<step>` in the state directory, kept as long as the verdict, which names it (`results`). More than 10 MB from one step and none of it is kept (`RESULTS-DROPPED` in `verdicts.log`). |
| `CHECK_CPUS` | The run's share of the CPUs: those the check may run on, divided by the slots (the fast lane too) holding a run when it starts, itself included; never less than the CPUs divided by `slots` (plus one with `fast_lane`), and at least 1. A run keeps its share to the end. A test runner sizes its processes or threads from it, so slots running at once fit the host. |

A value the host already sets wins.

### What `depends` records and passes on

A green traced run of the default branch's head writes
`depends/<tree>.json` in the build check's state directory:
`{"tree", "commit", "steps", "at", "tracer", "reads": {step: [path]},
"dirs": {step: [directory]}, "writes": {step: [path]}}`, paths relative
to the worktree, `.` for its top. `reads` is every path the step
opened, executed or looked for (found or not), less `.git` and build
caches; `dirs` is each read path's directory and each read path that
is a directory. `writes`, the step's write set (designs/0018), is every
path it opened to write or create, and every directory, link or rename
target it created; a record from before write sets has none. Records
are kept 14 days, and the newest always.

A pull request's steps that run with a record get two variables, so a
project's own test runner can narrow further:

| Variable | What it is |
| --- | --- |
| `CHECK_BASE` | The merge base the head was compared with. |
| `CHECK_DEPENDS` | The record's path. |

Neither is set when every step runs because nothing could be selected:
such a step must test everything.

### The fast lane

With `fast_lane`, each tick works out, in the cron checkout, the steps
each waiting pull request's head would run, as the worker would
(fetching the head there first if it isn't), and keeps the answer with
the head. A step's cost is the median of its last 20 runs on this host,
from the verdicts (`step_seconds`, each step's seconds); a step with
fewer than 5 runs has no cost. A head is cheap when every step it would
run has a cost and they add up to at most `max_seconds`. A head whose
steps can't be worked out yet (no record, a fetch or diff that fails)
is not cheap, and waits in the ordinary queue.

The lane is one more slot, `worker-lane.lock`, with its own worktree
(`wt-lane`) and build caches (`<directory>-lane`), and the same memory
limit and CPU weight. It runs only cheap heads, oldest first. An
ordinary slot takes a cheap head only when no other head waits.
`worker.log` names the lane (`=== ... fast lane ===`). A lane run whose
steps take more than three times `max_seconds` is not stopped, but
logged as `SLOW-LANE` with each step's time. What is posted is the same
as for any run.

### Pruning the build caches

The caches `caches` names only grow: a build tool such as Cargo keeps
every test executable it has ever linked, one per change of its
inputs, and never removes them. With `cache_limit`, each run prunes its
slot's caches as it finishes, while it still holds the slot:

1. executables (no extension) directly in a `deps` directory, older
   than a day, that the default branch's last green run in the slot
   didn't write (written between its start and end; 3 hours from its
   start when no end was recorded), each with its split debug info
   (`<name>.*.dwo`); and any `.dwo` older than a day whose unit has
   nothing left in the directory but its `.d`;
2. directories directly in an `incremental` directory with nothing in
   them touched for 7 days;
3. then, until the slot's caches hold at most `cache_limit`, whole
   directories directly in an `incremental` directory, each aged by its
   newest file, oldest first. Only while they hold more than one and a
   half times `cache_limit` does it go on to whole directories directly
   in a cache (a build profile such as `debug`, or a target with its
   profiles), since each of those is a cold build. Between the limit
   and that, the run logs `CACHE-OVER-LIMIT` with how much is over.

Pass 3 never removes a single file. A build tool trusts what it
recorded about the files it wrote, so a file missing from a directory
it still uses can break the build: removing the oldest files one by one
hollowed out Cargo's incremental sessions, and rustc crashed. A missing
directory only makes it build that part afresh, so pruning costs time,
never a wrong verdict.

Links are never followed, so nothing outside the caches is touched.
What went is logged in `verdicts.log` as `CACHE-PRUNED`, with the bytes
freed and left.

### Checking the merge with the default branch

With `merge_default`, the worker merges a pull request's head with the
default branch's tip, as the cron checkout last fetched it, and runs
the steps on that merge. So the steps, which come from the default
branch, and the files they run on are of one snapshot: a change to the
steps can't break heads based before it. The merge is a local commit,
never pushed, with both as parents and a fixed identity and date, so
the same inputs give the same commit. A head that already contains the
tip is checked as itself.

The verdict is the head's, posted on the head as without it, and also
records the tip (`base`) and the merge (`merged`). Its `tree` is the
merge's, so with `merge.test_ahead` the head the Merger's update makes
is posted green unrun, while the tip hasn't moved. With `depends`, the
steps are chosen against the tip, which is `CHECK_BASE`. The
description says `with <branch> at <sha>`, and `(itself red here)`
when it is red and the tip's own run here was red too.

A head that conflicts with the tip runs nothing. It is recorded red
(`failed` is `merge`, `conflicts` the paths) and posted as `conflicts
with <branch> at <sha>`, with the first few paths; its author must
merge the default branch either way. The Merger's predictions and the
default branch's own heads are checked as they are.

### Reusing a step's verdict

With `reuse` (and `depends`), each step a pull request's head would
run gets an input digest, from the dependency record its steps were
chosen by:

* each path the step read, at the tree checked (the merge, with
  `merge_default`): its mode and blob, or that it is absent;
* the names in each directory it listed;
* `CHECK_BASE`'s tree, unless the step's entry says
  `"reads_base": false`. Whether a step reads the variable can't be
  traced, so a step is assumed to read it, and is reused only against
  the same tip of the default branch, until it says it doesn't;
* its entry in `steps`;
* its `version` command's output, when it has one: a command, run in
  the worktree with the step's environment, that prints the versions
  of what the step uses from outside the tree, such as a compiler, a
  runtime or system libraries;
* when it read a path the tree doesn't have, the digests of every
  earlier step, since such a path is another step's output (the
  worktree is cleaned first) and which step wrote it isn't recorded.
  With `chain_writes_only`, see below.

Three switches, each off by default since each changes which steps
run, make reuse work for a project whose records went stale or whose
digests all chain to the base (designs/0018). Turn on all three
together, after one traced default-branch run with a release that
records write sets:

* **`trace_matched_main`** (with `main: "every"`): a default-branch
  head that would be matched (`MAIN-GREEN … matched`) first asks which
  steps lack a record, or have a stale one, at its tree, as `fresh`
  does; with `chain_writes_only`, a step whose record has no write set
  counts too. If any do, it runs traced instead (`MAIN-TRACE` in
  `verdicts.log`), at most once every `depends_minutes` in each slot.
* **`match_main_unrecorded`** (with `trace_matched_main`): without it,
  a default-branch head is matched only once its tree has a record,
  and a prediction writes none, so every merge is run in full. With
  it, the head is matched though its tree has no record, and
  `trace_matched_main` decides whether it runs traced instead, at most
  once every `depends_minutes` on the whole host (designs/0036).
* **`chain_writes_only`**: a path the tree doesn't have chains only the
  digests of the earlier steps that wrote it: it is in a step's write
  set or its `outputs`, beneath one of them, or a directory with one
  beneath it. Any other such path is just absent. Until the record has
  a write set for every earlier step, the step chains every earlier
  step's digest, as without the switch. A step's `outputs`
  (`["web/node_modules"]`) declare what it leaves behind out of the
  tracer's sight, such as through a statically linked program.
* **`dirs_by_names`**: a directory read with a file read beneath it
  counts its entry names, not its tree, so a change beneath it that
  the step never read doesn't stop the reuse. A directory with no file
  read beneath it keeps its tree: that is the only evidence of what
  the step read there.

A step is reused, not run, when the newest verdict here (this host,
these steps) with the same digest ran it green, or reused it, at most
`max_age_days` ago. The verdict records it under `reused`, pointing at
the run that ran it, and the description names the first:
`green on <host>: test as #458 at 3193656, same inputs`. These always
run: a step marked `always`, one the record doesn't know, one whose
`version` command fails, one that read another step's output when an
earlier step has no digest, and one whose newest verdict is red (a
timeout or an unclean exit included). The default branch's own runs
never reuse, and since they get no `CHECK_BASE`, nothing reuses them
unless the step says `"reads_base": false`.

### Keeping the records fresh

Without `fresh`, only the default branch's traced runs write records,
so everything the default branch changed since its last one counts as
changed for every pull request. With `fresh` (and `depends`):

* **Every green run writes a record** for the tree it checked (the
  merge, with `merge_default`), marked `"source": "run"`, its `from`
  saying where each step's paths came from. A step it ran gives what
  its trace read; a step it skipped gives the paths of the record it
  was chosen from, which still hold. A step that doesn't say
  `"reads_base": false` gives nothing, since one given `CHECK_BASE`
  may narrow its run, so the record lacks it. Such a record never
  replaces the default branch's own record for its tree. Each step
  keeps at most its newest 20 records.
* **Each step is chosen from its own record:** the first in which
  nothing the step read or listed changed between that record's tree
  and the base's, from the base's tree's records and then the newest
  20 others. That holds whether or not the record's commit is an
  ancestor: a merge checked with `merge_default` never is. A step no
  record answers for is stale and runs; the verdict's `selection`
  lists it under `stale`. `CHECK_DEPENDS` is then the records chosen,
  as one file (`depends/.selection-<slot>.json`, with `from`).
* **The default branch's head is traced only when it needs to be:**
  when a step is stale at its tree, or no record has it. It goes first
  while a waiting head runs a step only because it is stale (but not
  before the Merger's head when that has no stale step: the traced run
  would only delay the merge), and otherwise once it has waited
  `depends_minutes`, as before. While a head waits on it like that,
  the Merger's predictions (`merge.test_ahead`) don't go before it.
* **A head that would run a step only because it is stale waits**
  (`HELD` in the tick's output, `held` in `state.json`) while the
  default branch's head waits for its traced run or is running it, for
  at most `wait_minutes` from when the head was first seen; then it
  runs as now. Heads are estimated again whenever the tip or the
  records change, so a held head is chosen again once a fresh record is
  written, and goes to the fast lane if that is then cheap. The
  Merger's head and a cheap head never wait. Without `merge_default`,
  only a head whose merge base is the tip waits, since only then can
  the tip's record help it.

### `merge.stall_alert`

Without it, a stalled queue is a `STALLED` line in the Merger's log and
exit status 2. With it, the Merger also opens an issue for the stall,
messages agents through the XMPP relay (the `relay` section's `url` and
`token_file`, as `forge-router` posts; `$FORGE_ROUTER_RELAY` overrides
the url), and closes the issue, saying how, when the stall ends. A
`--dry-run` tick never opens, labels or closes an issue, or sends a
message.

| Key | Default | What it is |
| --- | --- | --- |
| `tell` | `[]` | The agents messaged, by name, at their session names. Only agents: the relay reaches sessions. `[]` opens the issue and messages nobody. |
| `retell_minutes` | `60` | How often the message is sent again while the stall lasts. A message no agent took is tried again on the next tick. |
| `owner_after_minutes` | `120` | When the issue gets `labels.waiting_on_owner`, counted from when the wait began, like `stall_minutes`. The label comes off when the stall ends. |
| `title` | `forge-merger: the queue is stalled on #{pr}` | The issue's title, `{pr}` the stalled pull request's number. |
| `labels` | `[]` | Labels the issue is opened with, such as an `Owner:` label so `forge-router` knows whose it is. A label GitHub refuses is tried again on the next tick. |

A new head, or another pull request first in the queue, is a new stall
with its own issue. `stall-alert.json` in the Merger's state directory
holds the open issue. Removing `stall_alert` stops the alerts but leaves
an issue already open for a person to close.

### `merge.test_ahead`

Under branch protection that requires a pull request to be up to date
with the default branch before it merges, the Merger takes one pull
request at a time: it updates it, waits for the build check, merges,
and only then starts on the next. The queue can drain no faster than
one merge per check run plus the Merger's ticks around it.
`test_ahead` keeps that rule and removes the wait. While the queue's
first pull request is being checked, the Merger works out the commit
each of the next few will have once those ahead of it merge, and the
build check tests those commits in its free slots. When the queue
reaches one of them, GitHub's update makes a commit whose tree has
already been tested green, the build check posts green on it at once,
and the Merger merges it on its next tick. Off without the key; `{}`
turns it on with the defaults.

| Key | Default | What it is |
| --- | --- | --- |
| `depth` | `3` | How many pull requests behind the one the Merger acts on are predicted, so `depth` + 1 commits in all. Deeper keeps more slots busy, but tests further into a queue that new approvals reorder. |
| `max_age_hours` | `24` | How long a green verdict counts for another commit with the same tree, on the same host with the same steps. The steps' digest can't see a toolchain upgrade on the host. |
| `spare_slots_only` | `false` | Whether a prediction runs only when no ordinary head is waiting, in a slot that would otherwise stay idle. Without it, predictions go before every head but the Merger's own, one that has waited `checks.max_wait_minutes`, and the default branch's once it has waited `checks.depends_minutes`. For a host whose slots are mostly full and whose queue reorders often, where most predictions go unused. designs/0014. |
| `batch` | `1` | How many predictions one run covers ([designs/0032](designs/0032-merge-in-batches.md)). With more than 1, only the batch's top, the last of the first `batch` predictions, is run; once it is green, each member's head is posted green as GitHub's update gives it the member's tree, and the Merger merges them one a tick with no run between. A red top makes its members the batch's suspects ([designs/0035](designs/0035-batches-take-stacks-run-side-by-side-and-size-themselves.md), second amendment), kept in `suspects.json`: they lead the queue and nothing joins in front of them or among them, a suspect that is pushed to, handed back, unapproved or closed leaves, and their prefixes are tested front to back at cut points (halving, with `batches` 1), merging the longest green prefix as they go, until one suspect is red on its own and is handed back. When every suspect merges green the Merger logs `BATCH-FLAKE`. Before predicting, each pull request's approval and `gate` tools are checked once per head, approval and description (`checked.json`), and one that fails is handed back at once rather than from the middle of a batch (designs/0035, fourth amendment). One a `gate` tool refused, at merge time or here, is predicted again once its title or body changes, at the same head and approval. 3 or 4 suits a queue that grows by a few an hour; at most `depth` + 1. |
| `batches` | `1` | How many batches are in flight at once ([designs/0035](designs/0035-batches-take-stacks-run-side-by-side-and-size-themselves.md) part 2). The Merger predicts up to this many end to end, each from the one before's top, and lists those after the first as `later` in `ahead.json`; `forge-pr-check` runs their tops, in order, only in a slot no ordinary head is waiting for. A later batch whose top's run has started is `fixed`; the first that isn't takes newly ready pull requests at its end, up to `batch`. Once the batch before has merged, a fixed one is the batch at its own size, and its top's green covers its members. Nothing follows a red top. While a red batch's suspects are searched, the later batches are the search's other cut points instead, `batches` - 1 of them (at least one), and the build check stops a run of a prefix longer than one already red (`stop` in `ahead.json`). `batches` × `batch` must be at most `depth` + 1. |
| `stacks` | `false` | Whether a stack's ready layers join their base's batch ([designs/0035](designs/0035-batches-take-stacks-run-side-by-side-and-size-themselves.md)). Each layer stacked on a predicted pull request, climbing the stack, that is eligible but for its base and whose approval covers its head, is predicted straight after it, so its base's batch covers it; it merges once retargeted, with no run of its own. A layer doesn't join a batch already being run, and a fork in the stack ends the climb. A queued pull request a layer pushes out of the batch is `owed` in `ahead.json`, and no layer may push an owed one out again. Needs `merge.stacks` and `batch` above 1. |

How it works, and why it is safe:

* **The prediction.** For the queue's first pull request, the Merger
  writes a local commit with the tree `git merge-tree --write-tree`
  gives for its head on the default branch's tip, and that tip as its
  parent: a squash of an up-to-date head has the head's tree. For each
  one after, it merges that pull request's head onto the commit before
  it, as GitHub's update will once the one before has merged. A
  conflict ends the list. The commits stay on the host, under
  `refs/agent-forge-merger/ahead/<k>` in the Merger's checkout, listed
  in `ahead.json` in its state directory. The build check reads them
  from the same object store, so the two must use the same checkout.
* **Green by tree.** The build check records each verdict's tree, host
  and steps. A head whose tree has a green verdict here, younger than
  `max_age_hours`, is posted `success` without a run, the description
  naming the commit it was tested as. A red verdict is never reused.
* **What it guarantees.** Nothing a merge requires changes: the head
  is still up to date, the status is still on exactly that head, and
  green is only ever posted for a tree a real run turned green. If
  GitHub's merge ever gives a different tree, that tree has no verdict
  and the head is checked as without `test_ahead`. Nothing more is
  written to GitHub.
* **What it costs.** Build time spent on predictions thrown away: a
  hand-back, or a push to a pull request in the list, spends every
  prediction behind it, at most `depth` + 1 runs. Other pull requests'
  checks wait behind the predictions, at most `depth` + 1 runs each
  time the queue or the default branch moves. A flaky test is accepted
  once per tree rather than once per head; a green after a red on the
  same tree is logged `FLAKE`.
* **When not to use it.** The steps must depend on the tree alone. A
  matched head is never checked out itself, and a prediction's history
  is not the head's, so a step that reads commit messages, authors or
  history can pass on one commit and fail on another with the same
  tree. Run such a check in `merge.gate`, on the real head, or leave
  `test_ahead` off. Without branch protection that requires heads to
  be up to date, the queue isn't serial and there is no wait to remove.
* **Batches.** With `batch` above 1, the default branch's tip is still
  always a tree a run turned green once a whole batch has merged, but
  the commits between are not run on their own: if a later member
  fixes what an earlier one broke, the default branch is broken for
  the minute or two between those merges. A member's head is posted
  `success` as `green on <host>: covered by batch #a..#b (tested as
  <sha>)`, and the Merger merges it only while that batch still holds
  (its later members unchanged and still queued); otherwise it waits
  for the new batch's top. The default branch's own check is the
  backstop if a batch stops halfway.
* **The queue doesn't churn.** With `test_ahead`, a pull request the
  Merger last updated or waited on keeps its place: a blocker isn't
  promoted past it, since that would spend the run it is having and
  every prediction behind it.

### `merge.hold_conflicting`

An author with several approved pull requests that change the same
lines, such as a generated file each one regenerates, otherwise goes
round after every merge: each of the others now conflicts with the
default branch, is handed back, rebased, approved again and rebuilt,
and the next merge does it all again. With `hold_conflicting: true`,
each tick compares every pull request in the queue but the first with
the ones ahead of it that are not held this way, by `git merge-tree
--write-tree` of the two heads. One that conflicts with one ahead (Q)
is held instead (`conflicts with #Q, ahead of it in the queue`): not
updated, merged, handed back or predicted. The Merger says so on it
once per head and Q, asking the author to leave it until Q merges;
then it is judged as usual, and handed back once if it still conflicts.
So each merge costs the author one rebase, not one per pull request.
Off by default.

### `merge.yield_after_hand_back`

The queue is taken blockers first, then oldest first, so a pull
request handed back to its author goes straight back to the front once
it is fixed and approved again. If it fails again, everything behind it
waits another update and build, and with `test_ahead` every prediction
behind it is spent too. With `yield_after_hand_back: N`, a candidate
handed back to its author K times in the last 24 hours is moved back N
times K places after that order, K at most `yield_cap` (default 3),
less one place for each other pull request that has merged while it
was a candidate since its latest hand-back. So the yield is spent:
once N times K others have merged ahead of it, it takes its place in
the order again. (Before v0.4.34 the yield was taken afresh from every
tick's order, so the oldest candidate, first in that order, could not
reach the front while N others were ready, for a whole 24 hours.) The
count is of distinct heads and reasons in the Merger's own record
(`merger.jsonl`, or `dry-run.jsonl` for a dry run), so a hand-back a
tick repeats counts once. A hand-back to the approving reviewer (the
approval no longer covers the head, so a fresh review is wanted), a
hold, a wait and a stall don't count. It moves within its group: a pull
request another is blocked by is never moved behind one that isn't,
and none past the group's end. With `test_ahead`, the one a run is in
flight for keeps its place. The tick's record says, under `yielded`,
who moved back how many places. Off by default.

With `yield_ignores_conflicts: true`, a hand-back for a conflict with
`main` doesn't count. A conflict is mostly what merged after the
approval, not the pull request's own failure, and counting it moves
back the very pull request others keep overtaking.

### `merge.early_conflicts`

The Merger stops at the first candidate it acts on, so one behind it
that a merge has left conflicting with the default branch hears nothing
until it reaches the front. With `early_conflicts: true`, after each
tick every candidate it didn't reach whose head conflicts with the
default branch's tip (`git merge-tree`) is handed back to its author at
once. The comment and reason are the same as at the front, it is said
once per approval and head, and it is recorded with `"early": true`.
A held candidate isn't checked: its hold already covers it. Off by
default (designs/0012).

### `merge.code_owners`

With `code_owners: true` (designs/0003), a candidate that changes,
since the default branch, a path its CODEOWNERS file gives owners is
held until, for each such path, one of its owners wrote the pull
request or has approved it: their newest review is `APPROVED`, at a
commit that covers the head as a reviewer's approval must. The file is
the default branch's `.github/CODEOWNERS`, `CODEOWNERS` or
`docs/CODEOWNERS`, the first found, read as GitHub reads it (the last
matching pattern wins). Owners are `@login`s: a path owned by a team or
an email waits until someone merges it by hand, since the Merger can't
check those. A pull request whose changed paths git can't list waits
too. `held` says `waits for code owner @owner: path`. It is a hold, so
nothing is posted and the author is asked for nothing. Off by default.

### `merge.age_first`

`{"after_minutes": N}` (default 90; designs/0037). A tick remembers
when it first saw each pull request as a candidate
(`waiting-since.json` in the Merger's state directory,
`dry-run-waiting-since.json` for a dry run). The time survives a
hand-back and a new head, and is forgotten when the pull request merges
or closes, or hasn't been a candidate for 24 hours. A candidate that
has waited N minutes is overdue, and the tick's record lists it under
`overdue` with the minutes waited. An overdue candidate:

- goes ahead of every other candidate but the one a `test_ahead` run is
  in flight for, urgent ones and the members of a batch being merged,
  the longest waiting first, ahead of those already predicted;
- is never predicted past while it conflicts with one predicted ahead
  of it: the prediction ends there;
- isn't held for a newer candidate's `right_of_way`;
- never yields (`yield_after_hand_back`).

Off by default.

### `merge.right_of_way`

`{"after_conflicts": N, "minutes": M}` (defaults 2 and 60;
designs/0002). A pull request P handed back to its author for a
conflict with `main` at N heads in the last 24 hours has earned right
of way. It has it for its head H while H is clean against `main`, H
wasn't handed back for anything else, and less than M minutes have
passed since a tick first saw H with it earned (`right-of-way.json` in
the Merger's state directory, `dry-run-right-of-way.json` for a dry
run). Meanwhile every other candidate whose head conflicts with H is
held, `conflicts with #P, which has right of way`, and told once per
head, as `hold_conflicting` tells; P goes first once it is a candidate,
after only the one a `test_ahead` run is in flight for, and never
yields. Of several that earned it, the first in the queue's order has
it. The tick's record says who has it under `right_of_way`. Off by
default.

### `merge.stacks`

`{"max_run": N, "wait_hours": H}` (`max_run` default 0, no limit;
designs/0025; `wait_hours` default none, designs/0033). A pull request
whose base branch is another open pull request's head branch is
stacked on it, and held as `stacked on #Q` rather than as based on
another branch, so it can be reviewed and approved while it waits.
When a live tick merges Q, the open pull requests stacked on it are
recorded (`stacks.json` in the Merger's state directory) and the run
goes on: the next tick retargets such a layer to the default branch
(`RETARGET`, `STACK #N: retargeted to main after #Q merged; K above`)
when it is eligible but for its base, and once its base is the default
branch it goes first in the queue, after only the one a `test_ahead`
run is in flight for. While such a run is in flight, a layer that
isn't in that run's batch goes after the batch instead, first in the
next one, so it doesn't change the batch being run. It is then updated, checked and merged like any
other. A layer that isn't eligible ends the stack, and is held as
usual. With `wait_hours` (1 to 168), it stays in the stack instead,
held as `stacked on #Q, which merged; waiting for: <why>`, and is
judged again every tick: once it is eligible but for its base it is
retargeted, and takes its usual place in the queue rather than the
first (it became ready after those ahead of it), while the layers above
it still go first once it merges. The merged heads below it are kept
while it waits, and it counts its own `wait_hours` from when it began
waiting. After that it leaves the stack, held as without the setting,
and the Merger says once on it that it no longer retargets it, so its
author retargets it and asks for its approvals again (the record's
`stack_waited`). A layer whose base was merged without the Merger, by
hand on GitHub, is found and retargeted the same way: the Merger asks
GitHub about a branch that is no open pull request's head, once an
hour while none from it has merged (`merged-elsewhere.json`). A merge of the layer below's branch into a layer, made after
its approval, covers its head when it is a clean merge and that
branch's head has merged in this stack. When the default branch
conflicts with a retargeted layer that lacks the final head of the
layer below, and merging that head into it is clean, the Merger makes
that merge on the layer's branch first (`RESTACK`, `STACK #N: merged
#Q's head <sha> into it`), through GitHub's merges API, once per
approval and head (`restacked.json`), and posts that no review is
needed again; the next tick updates it as usual. If that merge
conflicts, the hand-back says so, and to merge that head first. A
layer that holds that head but still conflicts with the default branch,
because the squash on it has none of the layer below's history, gets
the default branch merged in against that head instead of the old
merge base (`STACK-UPDATE`, `STACK #N: merged main into it against #Q's
head <sha>`), when that is clean and the default branch's first-parent
history has a commit with exactly that head's tree: committed as the
Merger and pushed with a lease on the head, once per approval and head
(`stack-updated.json`). Such a merge covers the layer's approval too,
since `covers_head` makes it again from its parents. So
authors don't merge or rebase a layer by hand after the layer below
merges unless it is handed back. With `max_run`, after N layers
merged in a row the next one keeps its usual place in the queue. The
tick's record says which stack is in progress under `stack`. Off by
default.

### `merge.resolve`

`[{"paths": [...], "command": [...], "check": [...]}]` (designs/0026).
When every path of a candidate's conflict with the default branch is in
one rule's `paths` (generated files), the Merger resolves it rather
than handing it back (`RESOLVE`): in a scratch worktree of the head it
merges the default branch, runs `command` there (a relative first
argument is run from that worktree), commits the result as the
Merger's App and pushes it to the head branch with a lease. It tries
once per approval and head (`resolved.json` in the state directory);
if a step fails, the pull request is handed back for the conflict, with
the step named. A merge of the default branch whose only non-clean
paths are one rule's `paths` then still covers the head, once `check`
exits 0 in a worktree of that merge (the verdict is kept per commit and
rule in `resolve-checked.json`). Any other conflict is handed back as
before. Off by default.

### `merge.hold_labels`

A list of label names. A pull request carrying one is held, `labelled
<name>`. Nothing is posted. Empty by default.

### `merge.blocked_by`

`"body"` (the default) reads `Blocked-by:` lines from the pull
request's body only. With `"everywhere"`, the body, its comments and
its reviews (`Not-a-review` comments included) are read, and the
newest of them with a `Blocked-by:` line decides: every strict
`Blocked-by: #N` line in it counts, `Blocked-by: none` clears, and a
line that starts `Blocked-by:` but doesn't parse holds the pull
request, `unparsed Blocked-by line: ...`, since it was meant as a hold.

### `merge.said_since`

`{"author": bool, "approvals": bool, "quiet": [names]}`. With
`author: true`, the author's own remarks after the approval hold it as
anyone else's do. With `approvals: true`, a later approval by anyone
whose approval doesn't count (not a reviewer App) is a remark too, and
holds it until a reviewer approves again. `quiet`
names more agents or machinery Apps whose remarks never hold, beside
the Merger's own. Off by default.

### `merge.update_once`

With `update_once: true`, after any hand-back under an approval (not
only one for a conflict) the Merger neither updates nor resolves the
pull request again until it is approved again: it is skipped,
`handed back under this approval (...); not updated until it is
approved again`. Off by default.

### `merge.ci_never_started`

Minutes. When no run of `ci_workflow` is listed for a head, the Merger
looks at the head's check runs: while they show the workflow starting
it waits; with none, it waits until this long after the head's commit
and then hands the pull request back, saying to close and reopen it
(which starts CI on the same head, so its approvals still count) or
push a new head, rather than reporting a stall. GitHub starts no CI on
a pull request it takes to conflict with the base, and a head it
judged wrongly keeps none until the pull request gets a new event.
Off by default.

### `merge.queued_stall_minutes`

Minutes. While the build check's state shows a head queued and not yet
running, that head stalls after this long, not after `stall_minutes`;
once running, its stall time counts from when it started. The state is
read from `--check-state DIR` (default the `pr-check` state directory
of the project). Off by default.

### `merge.read_as`

The name of an agent or machinery App whose token a `--dry-run` reads
with, instead of the Merger's, so a dry run beside a live one doesn't
share its App's rate limit. Live runs ignore it.

When `refs/pull/N/head` can't be fetched, the Merger always fetches
the head commit by its SHA before it gives up on it; there is no
setting for that.

### `router.idle_offer`

The default rules wake an agent when something happens to an item. An
agent that runs out of such events stops, even when it owns work it
could start. With `idle_offer`, each router run reads the relay's
`GET /directory`, and an agent shown idle long enough, with no turn
from the other rules, is offered the oldest issue it owns
(`Owner:<Name>`) that it can act on: not waiting on the owner or on
someone outside, nothing its `Blocked-by:` lines name still open, and
not referenced by an open pull request of its own (a closing
reference, or `#N`, its URL or "issue N" in the body). With
`shell_minutes`, an agent shown `shell` that long counts as idle too:
its turn has ended and only a background task it started still runs,
such as a wait loop that will never finish (designs/0017). Its offer
says since when the task has been running. One offer per idle period,
never reminded. An idle developer with nothing to act on is
reported to `report_to`, once per idle period. A directory that can't
be read, or isn't complete, makes nobody idle. Off without the key;
`{}` turns it on with the defaults. `agent_forge/rules_default.py`
(rules 15 and 16) has the detail.

| Key | Default | What it is |
| --- | --- | --- |
| `idle_minutes` | `10` | How long an agent's directory entry must have been `idle` before it is offered anything: long enough to skip the pause between two turns of one task. |
| `shell_minutes` | none (off) | How long an agent's entry must have been `shell` (its turn ended, a background task still running) before it counts as idle. Longer than `idle_minutes`, because the agent often acts on a background build when it finishes; 20 is a reasonable start. Needs agents and relay on an xmpp-mcp that reports `shell`. |
| `report_to` | none | The agent told about a developer (not a reviewer or a collaborator) that is idle with nothing it can act on, such as whoever plans the work. Not told about itself. Without it, nobody is told. |
| `hand_over_above` | none (off) | With `report_to`: when an agent owns more than this many issues it could act on but hasn't started (no `in-progress` label after `labels.status_prefix`), the report about an idle developer also names the agent owning the most, lists them (oldest first, at most ten), and asks for one to be handed over (designs/0038). An owner may keep this many for context; 3 is a reasonable start. Nothing is reassigned automatically. |

### `router.merge_notice`

No rule wakes an author when their pull request merges: it is closed,
so it is nobody's turn. An agent waiting on its own work to land then
waits until someone tells it. With `merge_notice`, each router run
that isn't `--only`'s reads the pull requests merged in the last
`window_minutes`, and tells each agent that wrote one, once: that it
merged, which open issues it leaves with nothing blocking them, and
the author's oldest issue it can act on (as the idle offer reads it).
Merges that can't be read are not taken as none. Off without the key;
`{}` turns it on with the defaults. `agent_forge/rules_default.py`
(rule 17) has the detail.

| Key | Default | What it is |
| --- | --- | --- |
| `window_minutes` | `30` | How far back each run looks for merges. Longer than the router's interval, so a run that fails or is late misses none; a merge older than this when the router first sees it is not announced. |
| `skip_label` | none | A label that makes a merged pull request tell nobody, such as one saying it needs no follow-up. |
| `to` | `"author"` | Whom a merge is told to: `"author"`, the pull request's author when an agent, or `"owner_label"`, the agent its owner label (`labels.owner_prefix`, `Owner:<Name>`) names, falling back to the author when it has none. A labelled name that isn't an agent is withheld and journalled, as every wake is. |

With `skip_label` or `to: "owner_label"`, the merged search also reads
each pull request's labels.

### `router.stuck`

Some pull requests are stuck in ways no other rule notices, so nobody
is woken for them: one stacked on a branch whose pull request has
merged, which the Merger holds as based on the wrong branch; one
blocked by the issue it closes, which the Merger can never take; and a
draft left idle with nothing saying what it waits for. Each key turns
on one rule that makes such a pull request its author's turn, with
what to do, said once for its event and never reminded. They come
before the other pull request rules, so they apply to drafts too.
Each is off without its key. `agent_forge/rules_default.py` (rules
0a, 0b and 0c) has the detail, and
[designs/0004](designs/0004-pull-requests-nobody-is-woken-for.md) the
reasons.

| Key | Default | What it is |
| --- | --- | --- |
| `retarget` | `false` | A pull request based on a branch other than the default, when no open pull request has that branch as its head: retarget it to the default branch. Said once per base. A project that bases pull requests on long-lived branches with no pull request of their own (release branches, say) leaves it off. |
| `self_blocked` | `false` | A pull request whose `Blocked-by: #N` line names an issue it closes: replace the line with `Closes #N`. Said once per edit of its body. |
| `draft_hours` | none | Hours a draft may go without a new commit, a body edit or a comment or review by its author before it is asked to be un-drafted or to record what it waits for. A draft records a wait with a `Blocked-by:` line naming an open item, a `Defer-until:` time still to come, or a label starting with `labels.status_prefix` and `waiting-` (`Status:waiting-*`), and is then left alone. Asked once per idle stretch. At least 1. |

### `router.owners`

Without it, an issue is an agent's when an `Owner:<Name>` label names
the agent, and some rules (answered by the owner, unblocked) fall back
to the issue's author while others (answered by anyone, the idle offer)
don't. With it, every rule reads an issue's owner the same way: the
agent the label names, else the author when the author is an agent,
else nobody. A label naming no agent is passed over. An issue nobody
owns, and not waiting on someone outside, is rule 18: `triage`'s turn,
"label it", said once for when it was opened or its `Owner:` labels
last changed, or surfaced when `triage` isn't set. `{}` turns on the
reading alone. Pull requests are unchanged: they are their author's or
a reviewer's, never by label.
[designs/0008](designs/0008-every-issue-has-an-owner.md) has the
reasons.

| Key | Default | What it is |
| --- | --- | --- |
| `triage` | none | The agent asked to label an issue nobody owns, usually whoever plans the work. Without it such issues are surfaced instead. |

### `router.unreviewed`

A pull request's lines that match no reviewer's `reviews` globs go to
the first reviewer listed, who may decline them as outside her area.
With `unreviewed`, each run that isn't `--only`'s also groups the
changed paths of open pull requests (drafts left out) that no
reviewer's globs cover by area: the first segment of a path, or its
first two under a `depth2` directory, and `/` for a file at the top.
An area with no open issue opens one, titled `No reviewer covers
<area> (first seen on #N)`, listing the pull requests and their paths,
labelled `<labels.owner_prefix><owners.triage>`, and makes it the
triage owner's turn to give the area a reviewer. A later pull request
is added to the open issue in a comment. Each pull request is told
once, in a `Not-a-review` comment naming the issue. While open pull
requests still change the area, the issue is reminded of them at most
once a day. The issue is closed by hand once the configuration covers
the area. Which reviewer a pull request goes to doesn't change.
`unreviewed.json` in the router's state directory records each area's
issue. Refused without `router.owners.triage`; `{}` turns it on with
the defaults. `agent_forge/rules_default.py` (rule 19) has the detail,
and [designs/0027](designs/0027-a-path-no-reviewer-covers-raises-an-issue.md)
the reasons.

| Key | Default | What it is |
| --- | --- | --- |
| `ignore` | none | Globs, matched as the reviewer choice matches `reviews`, of paths no reviewer needs to cover, such as a configuration directory a code owner reviews (`[".agent-forge/**"]`). Each a non-empty string. |
| `depth2` | none | Top-level directories whose areas are two segments deep, such as one holding many packages (`["crates"]`: `crates/foo/` is an area of its own). Each a directory name with no `/`. |

### `router.quiet_labels`

Labels that make an open item nobody's turn whoever owns it, such as a
parking label, a label for work done elsewhere, or a liveness marker.
An item with any of them is nobody's under every default rule,
suppressed `quiet: <label>` (the first of the list it has): no turn,
no reminder, no offer of it to an idle owner, and no notice of it
(rule 19). A list of label names; none by default. A project's own
rules module reads the same list from the project `configure` is
given, `project.section("router")["quiet_labels"]`.

### `router.forget_minutes`

The router forgets a turn as soon as a run doesn't decide it, so an
item decided on one run, not on the next, then decided again with the
same event is woken twice. With `forget_minutes`, a turn no run
decides is kept until that many minutes after the last run that
decided it: decided again with the same event within them, it is the
same turn, not woken again, and its reminders count on. It isn't
reminded while it is undecided. An `--only` run then forgets nothing.
Each recorded turn keeps `decided_at` in `state.json`; one recorded
before has its `woken_at` used. An integer, at least 0; 0, the
default, forgets at once. docs/router.md has the detail.

### `checks`

| Key | Default | What it is |
| --- | --- | --- |
| `context` | `vm/build-and-test` | The commit status the build check posts and the Merger requires. |
| `poster` | `merge.merger` | The App that posts it; the Merger counts only its statuses. |
| `steps` | one step, `make test` | What runs on each head, in order: `{"name": ..., "run": [argv], "only_if": path, "always": bool, "version": [argv], "reads_base": bool, "outputs": [path], "after": [name], "alone": bool, "light": bool, "caches": {...}}`, `only_if` a file the head must have; `outputs`, paths inside the worktree the step leaves behind that the tracer may not see it write (`reuse.chain_writes_only`); with `depends`, `always` runs the step whatever changed, for a cheap step or one that depends on more than files (a statically linked tool the tracer can't see). `after`, `alone`, `light` and `caches` say how the step runs beside others, and mean nothing without `side_by_side`. They are left out of the step's input digest. |
| `caches` | none | `{variable: directory name}`: each slot's own build cache, kept in the state directory, such as `{"CARGO_TARGET_DIR": "cargo-target"}`. |
| `cache_limit` | none | The most a slot's `caches` may hold after a run, as a size such as `40G` (`K`, `M`, `G` or `T`); with it each run prunes its slot's caches (see below). Without it nothing is pruned. |
| `slots` | `2` | Checks run at once (`$FORGE_PR_CHECK_SLOTS` overrides). |
| `memory_max` | `16G` | Each step's memory limit, in systemd's syntax, or `off`; `null` starts no scope and says nothing (`$FORGE_PR_CHECK_MEMORY_MAX` overrides). |
| `cpu_share` | `"run"` | When each step's `CHECK_CPUS` is worked out: `"run"`, once as the run starts, from the slots busy then; `"step"`, as each step starts, from the slots busy then, so a long step that starts after short ones gets the CPUs runs that have since finished let go. A `CHECK_CPUS` the host sets wins either way. designs/0029. |
| `side_by_side` | none | `{"max_steps": 4}` (the default): a run's steps run side by side, at most `max_steps` at once, each as soon as the steps its `after` names have finished (a name the head doesn't run is ignored). `alone` steps, timings, run after all the others, one at a time in their order, with nothing else of the run beside them. The run's CPUs are divided between its steps running that aren't `light`, at least 1 each; a `light` step, one that mostly waits, gets 1. A step's `caches`, `{variable: directory name}`, overrides `caches` for that step, per slot, pruned with the rest. Steps that write the same files in the worktree must name each other in `after`. A step's CPU time is its own process tree's; the verdict says `"side_by_side": true`, its `seconds` are how long the steps took together, and `step_alert` compares a step only with runs in the same mode. A step that isn't `alone` can't wait for one that is, an `alone` step only for an earlier `alone` one, and `after` can't make a cycle. designs/0029. |
| `cpu_weight` | `1000` | Each step's scope's `CPUWeight` (systemd's default is 100), so the check gets most of the CPU when agents on the same host build too; `0` sets none. |
| `merger_cpu_weight` | none: off | The `CPUWeight` of each step's scope in the run the next merge waits on ([designs/0031](designs/0031-the-next-merges-run-gets-the-cpu.md)): a head of the pull request in the Merger's `next.json`, or with `merge.test_ahead` its first prediction in `ahead.json`. Read as each step starts, so a run that comes to the front part-way gets it from its next step. Every other run, the fast lane and the default branch's runs keep `cpu_weight`. At least `cpu_weight` and at most 10000; 4000 to 10000 is the intended range. Needs `memory_max` (the steps' scopes). The verdict and its `VERDICT` line say `merger_weight`, and `step_alert` leaves those runs out. |
| `docker_slices` | none: off | `{"front": "<name>.slice", "other": "<name>.slice"}`, two slices the host has made: each step gets `CHECK_DOCKER_SLICE`, `front` in the run the next merge waits on (as `merger_cpu_weight` decides) and `other` in the rest, for its containers' `docker run --cgroup-parent`. A step that starts no containers ignores it. |
| `step_timeout_minutes` | `30` | When a step is stopped. |
| `merger_first` | `false` | Start first a waiting head of the pull request the Merger waits on (its `next.json`, read while under 10 minutes old), since the next merge waits on its verdict; every other head is then started oldest first, as without it. It only orders: a free slot is never kept empty for the Merger, whose wait may be on something no check here ends. `merge.test_ahead` turns it on, and refuses `false`. |
| `first_authors` | none | Agent names whose pull requests are checked first: a waiting head of one goes before every other pull request's, in the slots (even before the Merger's) and in the fast lane, the oldest of them first. A head that failed to prepare still goes behind, and the default branch's traced run still goes first when it is due. For a project's own build and tooling work, which speeds up every other check. |
| `depends` | `false` | Run each new head of the default branch in full under a tracer, recording what each step reads, and run a pull request's head with only the steps that read what it changes (designs/0005; see below). |
| `max_wait_minutes` | none | A pull request's head that has waited this long goes before every other pull request's, the oldest first: before `first_authors` and the Merger's predictions (`merge.test_ahead`). So priority can't starve an ordinary head beyond about this plus a few runs. While a head of the Merger's own pull request waits, it (and `first_authors`) still go first: nothing merges until it is checked, and in a deep queue every head is overdue. A head that failed to prepare is never overdue; the default branch's traced run still goes first when due or urgent. At least 1. |
| `main` | `needed` | Which default-branch heads are checked: `needed`, only a head a `depends` record needs traced (none without `depends`); `every`, every new head, in full (traced with `depends`), except one whose tree a green run here already passed, which is recorded green as matched (`MAIN-GREEN … matched`) without a run, and posted green on that commit naming the head whose run it reuses. With `depends`, a head is matched only once its tree has a record. designs/0011. `newest` (designs/0034, meant for an instance): as `every`, and the newest default-branch head goes before every pull request's head at once, rather than once it has waited `depends_minutes`; a tip overtaken while it waits is dropped, as with `every`. |
| `step_alert` | none | `{"growth": 1.5, "min_seconds": 30, "window_days": 7, "min_runs": 10, "labels": [...], "owner": ...}`, each optional (those are the defaults; no labels, no owner): an issue when a step gets slower. After each new verdict, a step with `min_runs` runs both in the last day and in the `window_days` before it (1 to 20) is slow when its last-day median is over `growth` (above 1) times its usual median and at least `min_seconds` more. Only the step's own runs count: not a matched verdict, a reused step or a failed one. A slow step opens one issue, written by `poster` with `labels` and `<labels.owner_prefix><owner>`, giving both medians, the default-branch runs either side of the jump and the commits between them; while it stays slow it gets fresh figures at most once a day, and when it is back under it is commented on and closed. Closed by hand, it stays closed until the step is back under. When more than half of at least 3 judged steps are slow at once, one issue about the host is opened instead, with the load and every step's figures. A write GitHub refuses is logged `REPORT-FAILED` and tried again on the next tick. designs/0019. |
| `main_red_issue` | none | `{"labels": [...], "title": ...}` (or one `label`): while the default branch is red, one issue is open about it, written by `poster` and signed `— <poster>` on its last line, after a line saying forge-pr-check wrote it. The first red head opens it, with the labels, naming the failed steps (with the end of their output) and the commits since the last green head; `title` is a template with `{sha}` (default "The default branch is red at {sha}"). Each further red head comments once; the first green head closes it. A write GitHub refuses is logged `REPORT-FAILED` and tried again on the next verdict. Refused unless `main` is `every` or `depends` is on. designs/0011. |
| `red_comment` | `false` | When a pull request's head is red, the poster comments on the pull request once: which steps failed, and the end of each one's output (the lines `worker.log` keeps, colour codes removed, at most 12,000 characters a step). For agents and people who can't read the host's log. Not for a prediction or the default branch (`main_red_issue` is that). It quotes test output, so turn it on only where everyone who can read the pull request may read that. |
| `host_fault_text` | none | Strings that, in a failed step's tail, make the red the host's fault, like a full disk, added to the built-in `No space left on device` and `ENOSPC` (`["registry returned an error"]`). Such a run records no verdict and is tried again later, and after 3 faults the head is posted `error`. The log's `HOST-FAULT` line quotes the string. For an outside service a step needs that is down, not the change's fault. Each a non-empty string. |
| `cache_fault_text` | none | A list of lists of strings, each naming a stale build cache by what the build tool says (`[["rust-lld: error:", "undefined symbol"]]`). A red whose failed step's tail holds every string of one list records no verdict; the log says `CACHE-FAULT`; every directory directly in an `incremental` directory under the slot's caches is removed while the slot is still held; and the head runs again at once, in the same slot. That isn't `labels.rerun`'s rerun, and it isn't a host fault. A second match stands red. Each list non-empty, of non-empty strings (designs/0022). |
| `keep_ignored` | none | Directories, relative to the repository's root, that the clean before each head leaves in place although git ignores them (`git clean -ffdx -e /<path>/`; `["web/dist"]`). Everything else untracked still goes. For a build's own output that a later build updates in place, touching only what changed, so a step that embeds it isn't rebuilt from nothing on every head. Only for output every step that reads it rebuilds first: a stale file kept here is read as if this head had made it. No wildcards and no `..`. |
| `drop_env` | none | Variables never passed to the steps, beyond `GH_TOKEN`, `GITHUB_TOKEN` and every `FORGE_` setting: each a name, or a prefix ending in `*` (`["INVOCATION_ID", "RECEIVER_*"]`). For variables a host's service passes on that a test must not see. |
| `depends_minutes` | `30` | How long the newest default-branch head waits behind pull requests' heads before it goes first, before the Merger's predictions too. |
| `tracer` | `preload` | How `depends` traces: `preload`, an `LD_PRELOAD` library built from lib/forge-trace.c with the host's C compiler (no root, cheap, blind to statically linked programs), or `strace` (everything, slower; needs strace installed). Go is the common case `preload` can't see: its toolchain and test binaries are statically linked, so a Go step would record almost nothing it reads and be skipped wrongly. A Go project uses `strace`. |
| `fast_lane` | none | `{"max_seconds": 60}`: one more slot, for heads whose chosen steps are cheap (designs/0006; see below). Refused without `depends`. |
| `fast_lane.max_seconds` | `60` | The most a head's chosen steps may together have taken here for it to be cheap. At least 1. |
| `merge_default` | `false` | Check a pull request's head merged with the default branch's tip, the tree GitHub's update makes, instead of the head alone (designs/0007; see below). |
| `reuse` | none | `{"max_age_days": 7}`: don't run a step on inputs a green run here already passed (designs/0009; see below). Refused without `depends`. |
| `reuse.max_age_days` | `7` | How long a step's green verdict stands for the same inputs, counted from the run that ran it. At least 1. |
| `reuse.trace_matched_main` | `false` | Run a default-branch head that would be matched traced instead, when a step's record at its tree is missing or stale, at most once every `depends_minutes` in each slot (designs/0018; see above). Refused unless `main` is `every`. |
| `reuse.match_main_unrecorded` | `false` | Match a default-branch head to a green run of its tree though the tree has no record, and let `trace_matched_main` trace it instead at most once every `depends_minutes` on the host, not in each slot (designs/0036; see above). Refused unless `trace_matched_main` is on. |
| `reuse.chain_writes_only` | `false` | A path a step read that the tree lacks chains only the earlier steps that wrote it (their write sets and `outputs`), not every earlier step (designs/0018). |
| `reuse.dirs_by_names` | `false` | A directory read with a file read beneath it is digested by its entry names, not its tree (designs/0018). |
| `fresh` | none | `{"wait_minutes": 15}`: every green run writes a dependency record, each step is chosen from its own freshest record, and a head that runs a step only because its record is stale waits for a fresh one (designs/0010; see below). Refused without `depends`. |
| `fresh.wait_minutes` | `15` | The longest a head waits for a fresh record before it runs as it would without `fresh`. At least 1. |
| `instances` | none | `{name: block}`: more instances of the check, each on its own host with its own context (designs/0021; see below). |

Only these keys are checked; the section may hold others, for tools
not here yet.

### `checks.instances`

A block describes one more instance of the check, for example on a
macOS host. The instance runs with `forge-pr-check --instance <name>`,
or with `$FORGE_PR_CHECK_INSTANCE` set. An unknown name is refused at
start (`NO-INSTANCE`), with the names that exist; it never falls back
to `checks`, which would post the first context from the wrong
platform. Without an instance, `forge-pr-check` runs as before.
[designs/0021](designs/0021-a-second-check-instance.md).

| Keys | Where they come from |
| --- | --- |
| `host`, `context`, `steps`, `poster` | The block. `context` and `steps` are required. `host` falls back to the top-level `host`, and `poster` to `checks.poster`. |
| `slots`, `memory_max`, `cpu_weight`, `step_timeout_minutes`, `caches`, `cache_limit`, `keep_ignored`, `drop_env`, `merge_default`, `red_comment`, `max_wait_minutes`, `host_fault_text`, `cache_fault_text`, `step_alert`, `cpu_share`, `side_by_side` | The block, else `checks`, else the default. |
| `depends`, `tracer`, `depends_minutes`, `fast_lane`, `reuse`, `fresh`, `main` | The block only, else the default: they describe the first host's tracer. |
| `main_red_issue` | The block only, never inherited: the instance's own issue while its newest default-branch verdict is red (designs/0034), titled by default `The default branch is red for <context> at <sha>`. Its `poster`'s App then needs `issues: write`. |
| `first_authors`, `merger_first`, `merger_cpu_weight`, `docker_slices` | `checks` only, and refused in a block: they belong to the context the Merger requires. `merge.test_ahead` doesn't apply to an instance either. |
| `pull_requests` | The block only, and refused in `checks`, whose context the Merger requires on every head: `{"paths": [glob, ...], "label": name}`, either left out but not both. See below. |
| `lock` | The block only: the host's own exclusive lock, a directory that agents sharing the machine take with `mkdir`, as `{"dir": path, "wait_minutes": n}` (`wait_minutes` default 60). Before its first step a run takes it and writes `owner` inside (`forge-pr-check <instance> #<pr> <sha> pid <pid> since <time>`), so an agent waiting on it sees who holds it. It lets go when the run ends, however it ends, and only while the owner file still names it. While someone else holds it the run waits, then after `wait_minutes` gives up as a `HOST-FAULT`, naming the holder. That fault isn't counted toward a head's `error`, since the host is busy rather than broken. |

Any other key in a block is refused. An instance's name is lower-case
letters, digits and hyphens. It keeps its own state directory
(`<state>/pr-check-<name>`), its `verdicts.log` lines carry its name
after the first word (`GREEN mac #7 …`), and its workers and chained
ticks are started with the same `--instance`. The Merger still requires
`checks.context` alone, so an instance's red can't stall merges.

**`pull_requests`: which heads an instance runs** (designs/0034). A
pull request's head is run only when the pull request carries
`label`, or changes a path one of `paths` matches; any other head gets
no status from this instance at all. The paths are GitHub's list of
the pull request's files since its merge base (both names of a
rename), read once a head and kept in `scope.json` in the instance's
state directory. A glob is matched by `fnmatchcase` on the path from
the repository's top, as a reviewer's `reviews` globs are, so `*`
crosses `/` too: `desktop/mac/*` is everything under `desktop/mac`. A
pull request GitHub lists 3,000 or more files for is run. One whose
files can't be read waits until they can (`SCOPE-UNKNOWN`). A head
left out says so once in `verdicts.log` (`OUT-OF-SCOPE`); if it had
been queued here, while labelled or before `pull_requests` was set,
its pending status is ended with a green `not run: this pull request
changes no path this check runs`. A label put on later queues the
head on the next tick. Without `pull_requests` every head is run, as
before.

Two instances on one host share `forge-cron-run`'s per-tool lock, so
their ticks run one after the other. Ticks return at once, so neither
waits long. Their state, slots and workers stay separate.

## The pin

`.agent-forge/pin`, beside the configuration, holds the agent-forge
commit the project approved: a full 40-character SHA, then anything
(such as the tag) for readers. When it exists, `forge-cron-run` reads
it from the synced cron checkout and refuses to run any tool, saying
`PIN-MISMATCH` and exiting 2, unless the installation it runs from is
at that commit. The receiver's ticks run through `forge-cron-run`, so
they are held to it too. A project that wants a pin bump reviewed as
carefully as a change of who may approve should give the file the same
code owner as the configuration.

`forge-install` keeps the installation at the pin. Run it directly from
cron (not through `forge-cron-run`, which would refuse it for the very
mismatch it is there to end). It checks out only the pinned commit,
never a tag. It refuses an installation with local changes or another
`origin`. It moves the installation only between jobs:
* `forge-cron-run` holds the installation's lock (`<install>.lock`,
  beside it) shared for each whole job, and hands it on to what the job
  starts. A forge-pr-check worker outlives its tick, so a check never
  runs part on one release and part on another.
* `forge-install` first leaves `<install>.install-wanted`, which stops
  new jobs starting (`INSTALL-PENDING`), then takes the lock
  exclusively. It waits up to `--wait` seconds (default 240), and
  otherwise exits 1 for the next run to wait again.
* A marker over an hour old was left by an installer that died, and is
  ignored.

With `--versions` it never waits ([designs/0015](designs/0015-install-beside-never-under.md)).
The installation is a symlink to `<install>.d/<sha>`, one clean clone
per release, and a new release is cloned beside the running one and the
symlink renamed over to it. Jobs that start afterwards run the new
release; running jobs keep theirs, and `forge-cron-run` puts the job's
own release first in `PATH` so that what it runs by name comes from the
same release. A release that is no longer current is deleted once it
has been retired for ten minutes and no job holds its lock. The first
`--versions` run on an installation that is still a directory moves it
into this layout once, waiting for jobs as above. Since nothing waits,
it can run every minute; a run with nothing to do reads only the pin
and the symlink.

## Where state lives

Each tool keeps its state in
`~/.local/state/agent-forge/<project>/<tool>/` (under
`$XDG_STATE_HOME` if set). `$FORGE_STATE_DIR` replaces
`~/.local/state/agent-forge` for tests and for hosts that keep state
elsewhere.
