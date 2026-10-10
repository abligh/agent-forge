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
| `merge` | no | How `forge-merger` merges: `merger`, the machinery App that acts (default `Merger`); `ci_workflow`, the Actions workflow every head must pass (default `ci`; `""` for none); `gate`, tools that must exit 0 before a merge, each an argument list with `{pr}`, `{base}` and `{head}` filled in, a `forge-` name run from the installation and anything else from the cron checkout (default `[["forge-commit-identity", "{base}..{head}"]]`); `stall_minutes`, how long a head may wait on its checks before the tick reports it stalled (default 30); `stall_alert`, whom a stall is told to (default none: a stall is only printed; see below); `test_ahead`, whether the next pull requests are tested ahead (default none: they aren't; see below); `hold_conflicting`, whether a pull request that conflicts with one ahead of it in the queue waits for it rather than being handed back after every merge (default `false`; see below); `yield_after_hand_back`, how many places a pull request handed back lately lets others go ahead of it, per hand-back (default none: nobody yields; see below), and `yield_cap`, the most hand-backs counted (default 3). The required status is `checks.context`, counted only when `checks.poster`'s App (default the merger) posted it. |
| `host_settings` | no | Files every agent host carries, installed by `forge-host-settings`: `files`, each `{"from": path in the project, "to": path under the home directory, "mode": "0644", "json": true when compared as JSON}`, and `host_keys`, keys a JSON file's host may set for itself. |
| `receiver` | no | How `forge-receiver` takes GitHub's webhook deliveries: `address` and `port` it listens on (default `127.0.0.1`, 8789), `path` (default `/hook`), `secret_file`, the webhook's secret, mode 0600 (default `~/.config/agent-forge/<project>/receiver-secret`), and `max_body`, the largest body in bytes (default 1048576); `ticks`, which tools each event runs, by GitHub event name (`issues`, `issue_comment`, `pull_request`, `pull_request_review`, `status`, `push`), each tool a name or an argument list, a `forge-` name run from the installation and anything else from the cron checkout (default none; `bin/forge-receiver` says when each event counts); and `route`, the router's argument list with `{items}` filled in as `N[,N]`, run for the items events name (default none: nothing is routed). |
| `docs` | no | The project's documentation, as the tools that read it see it; see below. |
| `checks` | no | The build check, `forge-pr-check`; see below. |
| `labels` | no | The labels tools read: `owner_prefix` (default `Owner:`; `Owner:<Name>` says whose an item is), `status_prefix` (default `Status:`), `waiting_on_owner` (default `Status:waiting-owner`: the owner's turn) and `waiting_external` (default `Status:waiting-external`: nobody's). |
| `relay` | no | Where `forge-router` posts wakes, and `forge-merger` its stall messages (`merge.stall_alert`): `url`, the xmpp-webhook-relay's base (default `http://127.0.0.1:8788`), `token_file`, its environment file holding `WEBHOOK_TOKEN` (default `~/.config/xmpp-webhook-relay/token`), `remind_seconds`, how long before a turn is woken again (default 7200), and `room`, the room the agents share. |
| `router` | no | How `forge-router` decides whose turn an item is: `rules`, the rules module (default `"default"`, the rules in `agent_forge/rules_default.py`), or a Python file in the project's own repository, by its path from the root, read from the cron checkout so it is reviewed there. docs/router.md is what a rules module provides. `idle_offer` turns on the default rules' idle offer, and `merge_notice` their merge notice (both below). Every key is checked. |
| `closing_keywords` | no | How `forge-closing-keywords` words a refusal: `enforced_by`, what refuses a pull request it fails (default `the Merger`), and `rerun`, how to run it again once GitHub's `closingIssuesReferences` catches up with a body edit (default `run the check again (the Merger does, on its next tick)`). A project that runs it in CI names its workflow and `gh run rerun --failed <run>`. The issues it judges are `repo`'s, and its default range starts at `default_branch`. The Merger runs it as a gate: `["forge-closing-keywords", "--pr", "{pr}", "--include-subject"]`. |

### An agent

The key is the agent's name: letters and digits, starting with a
letter.

| Field | What it is |
| --- | --- |
| `app_id`, `installation_id` | The agent's GitHub App and its installation on `repo`. `null` while the App doesn't exist yet: no tool mints a token for it. |
| `slug` | The App's slug, where it isn't the name followed by `naming.app_slug_suffix`, lower case. |
| `key` | The private key's file in `key_dir`, where it isn't `<name>.pem`. |
| `session_name` | The name the agent's session is woken by, where it isn't `naming.session_prefix` followed by the name. |
| `kind` | `developer` (the default) or `reviewer`. A reviewer's approval is what the Merger merges on. |
| `reviews` | A reviewer's path globs (`fnmatch`, so `*` crosses `/`): which pull requests are theirs to review. |

### `docs`

| Key | Default | What it is |
| --- | --- | --- |
| `paths` | `["*.md", "docs"]` | What `forge-doc-format`, `forge-doc-width` and `forge-doc-links` check when given no paths: globs relative to the checkout's top (`**` crosses directories), each a Markdown file or a directory searched for `*.md`. A glob that matches nothing is skipped, but matching no Markdown at all is an error. |
| `width` | `80` | The widest a prose line may be, for `forge-doc-width`. |
| `roles` | `docs/roles.md` | The roles file `forge-session-start` takes each agent's brief from. |
| `designs_dir` | `designs` | Where designs are, for `forge-approval-scope`. |

Only these keys are checked; the section may hold others, for tools
not here yet.

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
times K places after that order, K at most `yield_cap` (default 3). The
count is of distinct heads and reasons in the Merger's own record
(`merger.jsonl`, or `dry-run.jsonl` for a dry run), so a hand-back a
tick repeats counts once. A hand-back to the approving reviewer (the
approval no longer covers the head, so a fresh review is wanted), a
hold, a wait and a stall don't count. It moves within its group: a pull
request another is blocked by is never moved behind one that isn't,
and none past the group's end. With `test_ahead`, the one a run is in
flight for keeps its place. The tick's record says, under `yielded`,
who moved back how many places. Off by default.

### `router.idle_offer`

The default rules wake an agent when something happens to an item. An
agent that runs out of such events stops, even when it owns work it
could start. With `idle_offer`, each router run reads the relay's
`GET /directory`, and an agent shown idle long enough, with no turn
from the other rules, is offered the oldest issue it owns
(`Owner:<Name>`) that it can act on: not waiting on the owner or on
someone outside, nothing its `Blocked-by:` lines name still open, and
not referenced by an open pull request of its own. One offer per idle
period, never reminded. An idle developer with nothing to act on is
reported to `report_to`, once per idle period. A directory that can't
be read, or isn't complete, makes nobody idle. Off without the key;
`{}` turns it on with the defaults. `agent_forge/rules_default.py`
(rules 15 and 16) has the detail.

| Key | Default | What it is |
| --- | --- | --- |
| `idle_minutes` | `10` | How long an agent's directory entry must have been `idle` before it is offered anything: long enough to skip the pause between two turns of one task. |
| `report_to` | none | The agent told about a developer (not a reviewer) that is idle with nothing it can act on, such as whoever plans the work. Not told about itself. Without it, nobody is told. |

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

### `checks`

| Key | Default | What it is |
| --- | --- | --- |
| `context` | `vm/build-and-test` | The commit status the build check posts and the Merger requires. |
| `poster` | `merge.merger` | The App that posts it; the Merger counts only its statuses. |
| `steps` | one step, `make test` | What runs on each head, in order: `{"name": ..., "run": [argv], "only_if": path}`, `only_if` a file the head must have. |
| `caches` | none | `{variable: directory name}`: each slot's own build cache, kept in the state directory, such as `{"CARGO_TARGET_DIR": "cargo-target"}`. |
| `slots` | `2` | Checks run at once (`$FORGE_PR_CHECK_SLOTS` overrides). |
| `memory_max` | `16G` | Each step's memory limit, in systemd's syntax, or `off` (`$FORGE_PR_CHECK_MEMORY_MAX` overrides). |
| `step_timeout_minutes` | `30` | When a step is stopped. |
| `merger_first` | `false` | Start first a waiting head of the pull request the Merger waits on (its `next.json`, read while under 10 minutes old), since the next merge waits on its verdict; every other head is then started oldest first, as without it. It only orders: a free slot is never kept empty for the Merger, whose wait may be on something no check here ends. `merge.test_ahead` turns it on, and refuses `false`. |

Only these keys are checked; the section may hold others, for tools
not here yet.

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

## Where state lives

Each tool keeps its state in
`~/.local/state/agent-forge/<project>/<tool>/` (under
`$XDG_STATE_HOME` if set). `$FORGE_STATE_DIR` replaces
`~/.local/state/agent-forge` for tests and for hosts that keep state
elsewhere.
