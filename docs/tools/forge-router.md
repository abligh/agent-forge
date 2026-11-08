# forge-router

`forge-router` decides whose turn every open issue and pull request is,
and wakes that agent with one chat message. Agents don't poll GitHub;
nothing else tells an agent it is her turn. Each run reads every open
item with one GraphQL query, asks the project's **rules** whose turn
each item is, and posts one message per agent to an XMPP webhook relay,
listing the items that became hers. It remembers what it has woken, so
each turn is woken once, with reminders, and never twice for one event.
The engine (fetching, state, locking, delivery, the journal) is shared;
the rules are a module each project chooses ([router.md](../router.md)).

## Usage

    forge-router [--send] [--only N[,N]] [--seed] [--state-dir DIR]
                 [--checkout DIR]

It runs in three ways:

* **From cron**, every few minutes, through `forge-cron-run`, over
  every open item. It mints its own token as the Merger's App, so the
  job needs no `FORGE_CRON_APP`:

      */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
          $HOME/.local/bin/forge-cron-run forge-router --send

* **From `forge-receiver`**, within seconds of a webhook event, for the
  items the event names. The receiver's `route` setting is the argument
  list, with `{items}` filled in:

      "route": ["forge-router", "--send", "--only", "{items}"]

  Both kinds of run share one lock and one state, so an event's run and
  cron's run never wake the same turn twice.

* **By hand**, without `--send`, to see what it would do. A dry run
  prints every decision and every message, and sends nothing.

`--seed` is run once, by hand, when a project switches to this router
from one of its own (see below).

It needs the `gh` command on `PATH`: GitHub is read and written with
`gh api`.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--send` | off (a dry run) | Send the wakes, do the decisions' actions and record them. Without it, it prints the decisions and the messages it would send, takes no lock, and leaves `state.json` alone; it appends what it would send to `dry-run.jsonl`. |
| `--only N[,N]` | every open item | Route only these issues or pull requests, for a run started by one event. Decisions about any other item, or about no item, are dropped: those are cron's runs' to make. Numbers only, comma-separated. |
| `--seed` | off | The first run of a router taking over from another: decide as a sending run does, record every due turn as woken, send nothing. Refused once the state records a turn, and refused with `--only`. |
| `--state-dir DIR` | `~/.local/state/agent-forge/<project>/router` | Where state, the journal and the lock live. |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT` | The checkout whose `.agent-forge/config.json` it reads. The rules file, if the project has its own, is read from here too. With neither, it exits 2 (`NO-CONFIG`). |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`. |
| `FORGE_ROUTER_RELAY` | The relay's base URL, overriding `relay.url`. |
| `FORGE_ROUTER_REMIND` | Seconds before a turn is woken again by the default reminder, overriding `relay.remind_seconds`. Not a whole number: exit 2 (`BAD-CONFIG`). |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the state directory ([config.md, Where state lives](../config.md#where-state-lives)). |

## Configuration

It reads `.agent-forge/config.json` from `--checkout`
([config.md](../config.md)).

* `repo`, `owner`, `agents` (each agent's `session_name`, `twins`,
  `kind`, reviews and away settings; [An agent](../config.md#an-agent))
  and `naming.session_prefix` say who is who and where a wake goes.
* `merge.merger` is the App it reads and writes GitHub as (default
  `Merger`). It loads `forge-merger`'s own settings too, since the
  default rules use the Merger's tests of what an approval is.
* `relay`: `url`, `token_file` and `remind_seconds`
  ([Keys](../config.md#keys)).
* `router`: `rules` names the rules module; `forget_minutes` is the
  engine's
  ([router.forget_minutes](../config.md#routerforget_minutes)). The
  other keys turn on parts of the default rules:
  [idle_offer](../config.md#routeridle_offer),
  [merge_notice](../config.md#routermerge_notice),
  [stuck](../config.md#routerstuck),
  [owners](../config.md#routerowners),
  [unreviewed](../config.md#routerunreviewed),
  [red_first](../config.md#routerred_first),
  [nightly_wake](../config.md#routernightly_wake),
  [quiet_labels](../config.md#routerquiet_labels) and
  [issue_blocked_by](../config.md#routerissue_blocked_by).
* `labels` (the `Owner:` and `Status:` labels) and `checks.context`
  (the build check's status), read by the default rules.
* `nightly`, through forge-nightly's state: while the nightly is red,
  the pull requests that fix it are routed first.

### The rules

`router.rules` chooses the rules module:

* `"default"`, or no key: the rules shipped in
  [agent_forge/rules_default.py](../../agent_forge/rules_default.py).
  They read everything from GitHub: who wrote something by login, what
  a review saw by commit. Its docstring lists each numbered rule, such
  as "a request for changes stands and the author hasn't answered: the
  author's turn".
* A path, from the root of the project's repository, such as
  `"tools/router_rules.py"`. The file is read from the cron checkout,
  so it is reviewed like the rest of the project's code.
  [examples/rules.py](../../examples/rules.py) is a starting point: the
  default rules plus one more, which makes a pull request that
  conflicts with the default branch its author's turn.

[router.md](../router.md) is the interface a module provides
(`graphql`, `Roster`, `configure`, `decide`, and optional `sweep`,
`recheck`, `each_run` and others), and how the engine treats what it
returns. A module that can't be loaded is `BAD-CONFIG`, exit 2.

### The relay

Wakes go through an XMPP webhook relay running on the host, such as
`xmpp-webhook-relay` from xmpp-mcp. Each agent session is in the
relay's chat room under its session name, and the relay delivers a
post to `/agent/<session name>` as a chat message to that session.
The router reads the relay's shared token from `relay.token_file`
(default `~/.config/xmpp-webhook-relay/token`), an environment file
with a `WEBHOOK_TOKEN=` line.

## What it does

**One pass.** A run:

1. Mints the Merger's token (`NO-TOKEN` and exit 2 if it can't).
2. Reads every open pull request and issue, with the fields the rules
   ask for, then each lookup the rules declare (the default rules find
   what each `Blocked-by:` line names). When the rules want them, it
   also reads the relay's `GET /directory` (who is busy or idle) and the
   pull requests merged lately. One that can't be read is printed
   (`NO-DIRECTORY: …`, `NO-MERGED: …`) and handed to the rules as
   unknown.
3. Asks the rules for each item's decisions. A decision is a **turn**
   (the agent's to act on), a **notice** (for her information), a
   **relook** (worth another look), or a **surface** (nobody's, printed
   on every run, such as a pull request nobody can review).
4. On a sending run with a rules `recheck`, fetches the items about to
   be woken again and re-decides them. A wake that no longer holds is
   not sent (`withdrawn on recheck`).
5. Does each due decision's actions, such as adding a label, as the
   Merger's App, before delivery.
6. Sends one message per agent and records what was delivered.
7. Does the rules' once-a-run actions (`each_run`), on a sending run
   that isn't `--only`'s.

**Who is woken.** A turn is addressed to the agent's session name: her
entry's `session_name`, else `naming.session_prefix` and her name. It
also goes to each of her `twins`, or to her stand-in while she is
away. It counts as delivered when any one session takes it. A turn
that falls to someone who isn't in `agents`, such as the owner's own
pull request, is printed and journalled once as withheld, and not
sent.

**Once per turn.** A turn's key is the agent, the item and the event
that started it, as the rules say (a comment's time, a head SHA). A
new key is woken. An old key is woken again only as a reminder: by the
decision's own reminder policy, or after `relay.remind_seconds`
(default 7200). A turn no run decides any more is forgotten, or kept
for `router.forget_minutes` so an item that flaps is woken once.

**The message.** It starts `From: Router`, then `Your turn:` and one
line per item. Notices and relooks follow under
`For your information (not your turn):` and
`Worth another look (not your turn):`. A reminder's line ends
`(reminder)`. A pull request that fixes the red nightly is listed
first, and its line ends `(fixes the red nightly: first)`.

**Delivery.** Each message is posted as plain text to
`<relay.url>/agent/<session name>`, with the headers `X-Webhook-Token`,
`X-XMPP-Verbatim: 1` and a fixed `X-Webhook-Delivery` ID. It counts as
delivered only when the relay answers `200` with `"status": "queued"`
for one target, or `"duplicate"`. Anything else leaves the items due
for the next run:

* a `404` saying `unknown-name`: no session of that name is in the
  room now. Any other `404` means `relay.url` is wrong;
* a `503` with a `Retry-After` of 10 seconds or less is tried once
  more, with the same delivery ID, so the relay can't deliver it twice;
* no answer: the relay is down, and nothing more is posted this run.

**Lines worth knowing.**

| Line | Meaning |
| --- | --- |
| `SENT to <session>: #12, #14` | Delivered. |
| `NOT SENT to <session>: <why>; #12 stay due` | Not delivered; the next run tries again. |
| `NOT TAKEN by <session>: <why>; <other> took it` | One of several sessions refused it; another took it. |
| `WOULD SEND to <session>:` | A dry run's message, printed in full. |
| `#9 nobody's (rule 7: …)` | Nobody's turn, or a surface, and why. |
| `#12 Reviewer's, rule 8: already woken` | A dry run: the turn was woken before. |
| `… withheld, <name> is not an agent` | The turn falls to someone outside `agents`. |
| `ACTION FAILED: <action>: <why>` | GitHub refused a label write or similar; the wake still goes out. |
| `ROUTED 31 open, 3 turn(s) to wake` | The last line of a sending run, with counts of anything withdrawn, not sent or marked woken. |
| `DRY-RUN 31 open, …` | The last line of a dry run. |
| `BUSY: another run held <lock> for 60s` | Another sending run held the lock; this one did nothing. |
| `SEEDED 31 open, 12 turn(s) recorded as woken` | The end of a `--seed` run. |
| `SEED REFUSED: …` | `--seed` on a state directory that already records turns. |

**`--seed`.** A project switching from its own router starts with empty
state, so its first sending run would wake every open turn at once.
Run `--seed` once, just after the old router's last run. It decides as
a sending run does, records every due turn as woken (journalled as
`seeded`), and sends nothing, does no actions and no `each_run`. After
it, only turns that begin later are woken.

**Locking.** A sending run holds an exclusive lock on `lock` in the
state directory across read, decide, send and write. One that can't
have it within 60 seconds says `BUSY` and exits 0. A dry run takes no
lock.

## State and logs

In the state directory, `~/.local/state/agent-forge/<project>/router/`
unless `--state-dir` says otherwise:

| File | What it is |
| --- | --- |
| `state.json` | Every turn recorded as woken, by item: agent, rule, event, `woken_at`, the reminders sent, and `withheld` or `surfaced`. Written only by a sending run. |
| `wakes.jsonl` | The journal: every wake sent, failed, withheld, withdrawn, surfaced, marked woken or seeded, with the reason and the session; `each_run` failures as `"kind": "run"` records. |
| `previous.json` | Every decision the last sending run made, by item, for rules that want it. |
| `presence.jsonl` | Each change of a session's busy or idle status seen in the relay's directory, `{"at", "name", "status", "since"}`, `status` `gone` for a session no longer listed. Only a complete directory is recorded. `forge-stats` and `forge-flow` read it. |
| `presence-last.json` | The statuses last seen, so only changes are appended. |
| `dry-run.jsonl` | What each dry run would have sent or withheld, once per turn key. |
| `dry-state.json` | Which turn keys `dry-run.jsonl` already has. |
| `lock` | The sending runs' lock. |

A rules module may keep files of its own there; the default rules keep
`unreviewed.json` for `router.unreviewed`. The run's output goes to
standard output; from cron, `forge-cron-run` appends it to
`~/cron-logs/forge-router.log`.

## Exit status

* **0**: nothing was due, or all of it was delivered. Also every dry
  run, and `BUSY`.
* **1**: some was delivered and the rest stays due.
* **2**: something was due and none of it was delivered; or the
  question couldn't be asked: no configuration, a bad
  `FORGE_ROUTER_REMIND`, rules that couldn't be loaded or decided
  something the engine can't carry out, no token, GitHub failed, or a
  refused `--seed` (or `--seed` with `--only`).

A failed action never changes the status.

## See also

* [router.md](../router.md): the engine and the rules interface.
* [agent_forge/rules_default.py](../../agent_forge/rules_default.py)
  and [examples/rules.py](../../examples/rules.py).
* [forge-receiver](forge-receiver.md), which routes an event's items at
  once.
* [forge-cron-run](forge-cron-run.md), which runs it from cron.
* [forge-nightly](forge-nightly.md), whose fixes go first.
