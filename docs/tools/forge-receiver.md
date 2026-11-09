# forge-receiver

`forge-receiver` takes GitHub's webhook deliveries for a project and,
within seconds, runs the tools an event gives work to: the Merger after
an approval, the build check after a push to a pull request, the router
for the item a comment is on. The scheduled jobs run every few minutes
from cron anyway; the receiver only makes a merge, a build or a wake
follow its cause rather than the next tick. It decides nothing itself.
It says which tool to tick and which item to look at, and the tools
decide. Losing it loses latency, not merges or wakes.

## Usage

    FORGE_CRON_CHECKOUT=~/widget-cron forge-receiver [--port N]
                                                     [--checkout DIR]

It is a long-running HTTP server, run as a systemd user unit, one per
project on a host.
[examples/forge-receiver.service](../../examples/forge-receiver.service)
is a template:

    [Service]
    Environment=FORGE_CRON_CHECKOUT=%h/widget-cron
    ExecStart=%h/.local/share/agent-forge/bin/forge-receiver
    Restart=always
    RestartSec=5

Install it as `~/.config/systemd/user/widget-receiver.service`, then:

    systemctl --user daemon-reload
    systemctl --user enable --now widget-receiver

Its log is the journal: `journalctl --user -u widget-receiver`.
`Restart=always` is required: the receiver exits 0 by itself when its
configuration or its installation changes, for the service manager to
start the new one.

It listens on `127.0.0.1:8789` by default, usually behind a TLS proxy
that faces the internet. On GitHub, add a webhook to the repository
with that public URL and path (default `/hook`), content type
`application/json`, the same secret as `receiver.secret_file`, and the
events the `ticks` table names.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--port N` | `$FORGE_RECEIVER_PORT`, else `receiver.port`, else `8789` | The port to listen on. |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT` | The project's cron checkout, kept at the default branch by `forge-cron-run`. Its configuration is read at start, and every tick runs from it. With neither, it exits 2 (`NO-REPO`). |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`, and set for every run it starts. |
| `FORGE_RECEIVER_PORT` | The port, overriding `receiver.port`. `--port` overrides both. |
| `FORGE_RECEIVER_SECRET_FILE` | The secret file, overriding `receiver.secret_file`. |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the state directory ([config.md, Where state lives](../config.md#where-state-lives)). |
| `HOME` | What a leading `~` in a tick's arguments becomes. |

The runs it starts get its whole environment.

## Configuration

Its settings are the `receiver` section of `.agent-forge/config.json`
([config.md, Keys](../config.md#keys) lists them): `address`, `port`,
`path`, `secret_file`, `max_body`, `ticks`, `actions`, `route` and
`run_timeout`. It also reads `repo`, `default_branch`,
`checks.context` (which `status` events count) and `merge.merger` (the
App it maps a commit to its pull requests as). A short example:

    "receiver": {
      "ticks": {"pull_request": [["forge-pr-check", "--post"]],
                "pull_request_review": ["forge-merger"],
                "status": ["forge-merger"],
                "push": ["forge-merger"]},
      "route": ["forge-router", "--send", "--only", "{items}"]
    }

Each tool in `ticks` is a name or an argument list. A `forge-` name is
this installation's; any other is the project's `tools/`. A leading `~`
in any argument is the home directory.

The secret file must exist, be non-empty and be readable by its owner
alone (mode 0600), or the receiver won't start. Its default is
`~/.config/agent-forge/<project>/receiver-secret`. Make one with, for
example, `openssl rand -hex 32 > FILE && chmod 600 FILE`. The secret is
read once, at start, and never printed.

## What it does

**Checking a delivery.** It faces the internet, so nothing is parsed
before the signature. In order:

1. A `POST` to `path`, or `404` / `405`.
2. A `Content-Length` of at most `max_body` (default 1 MiB), at most
   eight ASCII digits, or `411` / `413`, before the body is read.
3. `X-Hub-Signature-256`, an HMAC-SHA256 of the body under the secret,
   or `401`. The older SHA-1 `X-Hub-Signature` is never accepted.
4. An `X-GitHub-Delivery` ID seen among the last 5000 is answered `202`
   and dropped. A delivery whose handling fails is forgotten again and
   answered `500`, so GitHub's redelivery can bring it back.
5. A body that is JSON, or `400`. So the webhook's content type must
   be `application/json`.
6. What the event gives work. Anything else, `ping` included, is
   answered `202` and ignored.

It answers `202` and never runs anything inside the request.

**Which events tick their tools.** An event in `ticks` gives its tools
work only when:

| Event | When |
| --- | --- |
| `pull_request` | The action leaves a new head: `opened`, `reopened`, `synchronize` or `ready_for_review`. |
| `pull_request_review` | An approval was submitted. |
| `status` | The status for `checks.context` went `success`. |
| `check_suite` | A suite `completed` with conclusion `success`. |
| `push` | To the default branch. |
| `issues`, `issue_comment` | Any delivery naming an item. |

`receiver.actions` narrows this further, by event. With
`{"issues": ["opened", "labeled"]}`, only those `issues` actions tick
`issues`' tools. It never changes what is routed.

**Routing.** With `route` set, the item each `issues`, `issue_comment`,
`pull_request` and `pull_request_review` delivery names is routed too,
and so is each pull request a `completed` `check_suite` lists, whatever
its conclusion. A `status` for `checks.context` names a commit; the
worker maps it to its open pull requests with one API call, as the
Merger's App (`LOOKUP-FAILED` or `NO-TOKEN` when that fails). Without
`route`, nothing is routed and no token is minted.

**Running.** A worker thread waits until 20 seconds have passed since
an item's latest event, so a burst on one item makes one run, but no
more than 60 seconds after its first. Then it ticks each tool due, in
the order the `ticks` table first names them, and then the router once
for all the items, `{items}` filled in as `N[,N]`. Each runs as

    forge-cron-run TOOL [ARGS...]

from the current installation, with an argument list, never a shell,
and `FORGE_CRON_CHECKOUT` set. So it runs from the reviewed checkout,
under the cron jobs' locks and pin check, logging where cron's runs
log. A run longer than `run_timeout` seconds (default 600) is stopped
and the worker carries on. An extra tick changes nothing but timing:
the Merger still does one update or merge per tick, and the router's
lock and state stop it waking a turn twice.

**Restarting itself.** Every 5 seconds it looks at the configuration
file and at its own file, followed through links, so a `forge-install`
of a new release counts. When either has changed, it logs `RESTART:`,
lets a run in progress finish, and exits 0. Items still waiting out
their 20 seconds are left to cron.

**Lines worth knowing**, on standard output (the journal):

| Line | Meaning |
| --- | --- |
| `listening on 127.0.0.1:8789/hook` | Started. |
| `202 pull_request <delivery id>: …` | One line per delivery: the answer, the event, the ID and why. |
| `ticking forge-merger` | A tool is run. |
| `forge-merger exit 0` | How it ended. |
| `TIMEOUT: forge-pr-check stopped after 600s` | A run went past `run_timeout`. |
| `routing #12, #14` | The router is run for these items. |
| `commit 1a2b3c4d5e: #12` | A `status` commit mapped to its pull requests. |
| `ROUTE-FAILED: …` | A worker run raised; the worker carries on. |
| `RESTART: <file> changed; …` | It is exiting for the service manager to restart it. |

## State and logs

* `~/.local/state/agent-forge/<project>/receiver/seen-deliveries.json`:
  the delivery IDs seen, so a restart remembers them.
* Its own log is standard output, which systemd puts in the journal.
* The runs it starts log to `~/cron-logs/<tool>.log`, through
  `forge-cron-run`.

## Exit status

* **0**: it stopped after a change to its configuration or
  installation (or was interrupted).
* **2**: it couldn't start: no checkout, no or a bad configuration, no
  usable secret file, or the port taken (`NO-REPO`, `NO-CONFIG`,
  `NO-SECRET`, `NO-PORT`).

Otherwise it runs until stopped.

## See also

* [forge-cron-run](forge-cron-run.md), which every tick runs through.
* [forge-router](forge-router.md), what `route` runs.
* [examples/forge-receiver.service](../../examples/forge-receiver.service)
  and [examples/config.json](../../examples/config.json).
