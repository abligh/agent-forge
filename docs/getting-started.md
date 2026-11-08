# Getting started

This guide takes a project from nothing to a working agent team: AI
agents that write and review pull requests on GitHub, a Merger that
merges what reviewers approved, a build check that tests every head
on your own host, and a router that wakes whichever agent's turn it
is. It assumes you know GitHub and a Linux shell, and nothing about
agent-forge.

The examples use a project called `widget` in the repository
`example/widget`, owned by the person `example-owner`, built on the
host `build.example.org`. Replace them with your own.

## How the pieces fit

```text
 agent sessions ──push / review──▶ GitHub ──webhook──▶ forge-receiver
 (Claude Code,                       ▲                    │ ticks
  one per agent)                     │                    ▼
       ▲                  statuses,  │   forge-pr-check (build check)
       │                  merges,    ├── forge-merger   (the Merger)
       │ wakes            comments   └── forge-router   (whose turn)
       │                                                  │
       └──────── XMPP relay ◀─────────────────────────────┘
```

* **Agents** are Claude Code sessions, each with its own GitHub App,
  so every commit, review and comment says which agent made it.
* **Reviewers** are agents whose approval the Merger merges on. Each
  reviews the paths its `reviews` globs name.
* **`forge-pr-check`** builds and tests each open pull request's head
  on your host, under a memory limit, and posts one commit status.
* **`forge-merger`** merges approved pull requests whose head is green,
  one train at a time ([below](#trains)), and hands back the ones that
  can't merge, with the reason, to their author.
* **`forge-router`** decides whose turn every open issue and pull
  request is, and wakes that agent's session through an XMPP relay.
* **`forge-receiver`** takes GitHub's webhook deliveries and runs the
  tools an event gives work to, so the team reacts in seconds. Cron
  runs the same tools as a backstop.
* **You, the owner,** approve designs and decisions. Agents never wait
  on you to press merge.

Everything a project says about itself is in one file in its own
repository, `.agent-forge/config.json` ([configuration](config.md)).
Each tool has a reference page under [tools/](tools/).

## What you need

* **A Linux host** for the build check and the scheduled jobs, with
  Python 3.11 or later, git 2.38 or later, `gh`, `openssl`, `flock`,
  cron and systemd user services (`systemd-run --user` puts each check
  under its memory limit). Run `loginctl enable-linger` for the user
  the jobs run as, so its systemd user manager runs when nobody is
  logged in. The agents' sessions can run on the same host or others.
* **A GitHub repository** you administer, and a GitHub account that
  can create GitHub Apps.
* **Claude Code** for the agents' sessions.
  [claude-agent-supervisor](https://github.com/abligh/claude-agent-supervisor)
  keeps each session running.
* **An XMPP server and the channel** the router wakes agents through:
  [xmpp-mcp](https://github.com/abligh/xmpp-mcp) gives each session an
  XMPP address and pushes messages into it, and its
  `xmpp-webhook-relay` takes the router's messages over HTTP. Without
  it everything else works, but agents are not woken: they must poll.

## 1. Install agent-forge

agent-forge is standard-library Python, installed by cloning it at a
release tag:

```sh
git clone --branch vX.Y.Z https://github.com/abligh/agent-forge \
    ~/.local/share/agent-forge
mkdir -p ~/.local/bin
ln -s ~/.local/share/agent-forge/bin/forge-* ~/.local/bin/
```

Pin the version your project runs by committing `.agent-forge/pin`:
the release's full commit SHA, then optionally its tag.

```sh
git -C ~/.local/share/agent-forge rev-parse HEAD   # put this in the pin
```

`forge-cron-run` runs nothing while the installation is at any other
commit, and `forge-install`, run from cron, moves the installation to
the pin once a change to it merges ([The pin](config.md#the-pin),
[forge-install](tools/forge-install.md)). Upgrading is then a reviewed
pull request that changes one line.

## 2. Create the GitHub Apps

Make one GitHub App per agent and one for the Merger, each installed
on your repository only. An App per agent is what keeps identities
apart: a token minted for `LeadDev` can't push or review as anyone
else.

Name each App `<agent name><suffix>`, all lower case, with one suffix
for the whole project: `leaddevwidgetagent`, `reviewerwidgetagent`,
`mergerwidgetagent`. The suffix goes in `naming.app_slug_suffix`
([forge-app-token](tools/forge-app-token.md#configuration) says how a
slug is worked out).

Give each App these repository permissions:

| App | Permissions |
| --- | --- |
| A developer agent | Contents: write, Pull requests: write, Issues: write, Actions: read, Commit statuses: read, Metadata: read; Workflows: write if it may change `.github/workflows` |
| A reviewer agent | Contents: write (so that its approvals count; it pushes nothing), Pull requests: write, Issues: write, Actions: read, Commit statuses: read, Metadata: read |
| The Merger | Contents: write, Pull requests: write, Issues: write, Commit statuses: write, Actions: write, Checks: read, Metadata: read; Workflows: write, since it merges the default branch into pull requests' branches and GitHub refuses that push from an App without it once `.github/workflows` has changed |

The Merger's App also posts the build check's status (`checks.poster`
defaults to it), re-runs a stalled Actions workflow, labels and
comments; `forge-nightly` dispatches workflows as it. No App needs
webhooks of its own: the repository's webhook goes to
`forge-receiver` (step 7).

For each App, generate a private key and save it as
`~/agent-keys/widget/<name>.pem`, mode 0600, on every host that mints
tokens for it. Note each App's id and its installation id (the number
at the end of the installation's settings URL).

Give the reviewer Apps' approvals weight: an App needs write access to
the repository (Contents: write is enough) for GitHub to count its
approval towards branch protection's required approvals. If you would
rather not, leave the required approvals at 0: the Merger merges only
on a reviewer agent's approval whatever branch protection says.

## 3. Write the configuration

Write `.agent-forge/config.json` in a checkout of the repository. A
minimal file for two developers, a reviewer and the Merger:

```json
{
  "project": "widget",
  "repo": "example/widget",
  "owner": "example-owner",
  "host": "build.example.org",
  "key_dir": "~/agent-keys/widget",
  "naming": {"app_slug_suffix": "widgetagent",
             "session_prefix": "Widget"},
  "agents": {
    "LeadDev": {"app_id": 101, "installation_id": 201},
    "UIDev": {"app_id": 102, "installation_id": 202},
    "Reviewer": {"app_id": 103, "installation_id": 203,
                 "kind": "reviewer", "reviews": ["**"]}
  },
  "machinery": {"Merger": {"app_id": 104, "installation_id": 204}},
  "checks": {
    "context": "build-and-test",
    "slots": 2,
    "memory_max": "16G",
    "steps": [
      {"name": "test", "run": ["make", "test"]},
      {"name": "docs", "run": ["make", "doc-check"]}
    ]
  },
  "merge": {"ci_workflow": ""},
  "relay": {"url": "http://127.0.0.1:8788",
            "token_file": "~/.config/xmpp-webhook-relay/token"}
}
```

Check it, then commit it to the default branch:

```sh
cd ~/widget && forge-config check
forge-app-token --list      # what the configuration says of each App
forge-app-token --check     # that every key and id agrees with GitHub
```

The Merger's entry under `machinery` must be called `Merger`, unless
`merge.merger` names another.

Every key is checked, and a key agent-forge doesn't know is an error,
since it is usually a misspelling. [config.md](config.md) explains
every key and its default; [examples/config.json](../examples/config.json)
is a fuller example.

What the minimal file gets you, by default:

* the Merger merges what `Reviewer` approved, once `build-and-test`
  posted by the Merger's App is green on the head;
* `ci_workflow: ""` means no GitHub Actions workflow is required as
  well (the default is a workflow called `ci`);
* the gate `forge-commit-identity` refuses commits made under a host's
  unconfigured git identity;
* <a id="trains"></a>**trains**: the Merger tests ahead. While the
  first pull request in the queue is being checked, it works out the
  commits the next ones will have once those ahead merge, and the
  build check tests those in its free slots. It groups the queue into
  trains of up to four pull requests that touch different files, two
  trains in flight, so one green run merges a whole train
  ([`merge.test_ahead`](config.md#mergetest_ahead),
  [designs/0050](designs/0050-trains-are-the-default.md)). Say
  `"test_ahead": false` under `merge` to merge one at a time.

Each agent's session is woken as `naming.session_prefix` plus its name
(`WidgetLeadDev`), unless its entry gives a `session_name`.

## 4. Labels

Create these labels in the repository:

| Label | Meaning |
| --- | --- |
| `Owner:<Name>`, one per agent | Whose an issue or pull request is. The router wakes that agent for it. |
| `Status:waiting-owner` | Waiting on you. The router wakes nobody; you find these with a label search. |
| `Status:waiting-external` | Waiting on something outside the project. Nobody's turn. |

Optional ones, each named in `labels` ([config.md](config.md#keys)):
an urgent label that puts a pull request first, and a rerun label that
runs a red head once more. Change any of the names under `labels`.

## 5. Protect the default branch

In the repository's branch protection (or a ruleset) for the default
branch:

* require a pull request before merging;
* require the status check `build-and-test` (your `checks.context`),
  and require branches to be **up to date** before merging. Testing
  ahead exists to remove the wait that this rule creates; without it
  there is no queue to speed up;
* allow the Merger's App to merge, and nobody to push directly;
* set the required approvals as step 2 says.

With a `CODEOWNERS` file and `"code_owners": true` under `merge`, a
pull request that changes a code-owned path also waits for its
owner's approval ([`merge.code_owners`](config.md#mergecode_owners)).
Use it for the paths that change how the team works, such as
`.agent-forge/` and the host files, so that agents can't change their
own rules unreviewed.

## 6. The cron checkout and the crontab

On the build host, clone the repository once more, for jobs only:

```sh
git clone git@github.com:example/widget.git ~/widget-cron
```

For a private repository, give the host a read-only deploy key to
fetch with, or set `FORGE_CRON_APP` on the cron lines so each job
fetches and calls GitHub as that App
([forge-cron-run](tools/forge-cron-run.md)).

Every job runs through [forge-cron-run](tools/forge-cron-run.md),
which resets this checkout hard to the default branch first, so a job
only ever runs reviewed code and configuration. Never work in it.

A crontab for the build host. cron doesn't expand `$HOME` in its
variable lines, so each line is written out in full; replace
`/home/build` with the home directory:

```sh
PATH=/usr/local/bin:/usr/bin:/bin

# Keeps ~/.local/share/agent-forge at .agent-forge/pin; never through forge-cron-run.
*/5 * * * * FORGE_CRON_CHECKOUT=/home/build/widget-cron /home/build/.local/share/agent-forge/bin/forge-install --versions
# The build check, the Merger and the router.
* * * * *   FORGE_CRON_CHECKOUT=/home/build/widget-cron /home/build/.local/bin/forge-cron-run forge-pr-check --post
*/2 * * * * FORGE_CRON_CHECKOUT=/home/build/widget-cron /home/build/.local/bin/forge-cron-run forge-merger
*/5 * * * * FORGE_CRON_CHECKOUT=/home/build/widget-cron /home/build/.local/bin/forge-cron-run forge-router --send
# Clear the build output of worktrees under --root nobody is using.
17 * * * *  FORGE_CRON_CHECKOUT=/home/build/widget-cron /home/build/.local/bin/forge-cron-run forge-prune-builds --root /home/build/agents
```

The logs are in `~/cron-logs/<tool>.log`. A router taking over from
another one starts with `forge-router --seed` once
([forge-router](tools/forge-router.md)).

To keep the crontab itself reviewed, commit it to the repository (for
example `tools/host/crontab.build`), name it under
`host_settings.crontabs` by the host's short name, and add a
`forge-host-settings --apply` line to it: once a change to it merges,
the host installs it by itself
([forge-host-settings](tools/forge-host-settings.md)).

## 7. Webhooks

`forge-receiver` makes the team react within seconds instead of at
the next cron tick. Add a `receiver` section:

```json
"receiver": {
  "ticks": {"pull_request": [["forge-pr-check", "--post"]],
            "pull_request_review": ["forge-merger"],
            "status": ["forge-merger"],
            "push": ["forge-merger"]},
  "route": ["forge-router", "--send", "--only", "{items}"]
}
```

The receiver reads its configuration from the cron checkout, so the
section must be merged to the default branch first. Then:

1. Make the webhook's secret:

   ```sh
   mkdir -p ~/.config/agent-forge/widget
   openssl rand -hex 32 > ~/.config/agent-forge/widget/receiver-secret
   chmod 600 ~/.config/agent-forge/widget/receiver-secret
   ```

2. Install
   [examples/forge-receiver.service](../examples/forge-receiver.service)
   as `~/.config/systemd/user/widget-receiver.service`, with its
   placeholders filled in, and start it:

   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now widget-receiver
   ```

   It listens on `127.0.0.1:8789` (`receiver.address`, `port`).
3. Put a TLS proxy in front of it that faces the internet.
4. On GitHub, add a webhook to the repository: the proxy's URL with
   the path `/hook` (`receiver.path`), content type
   `application/json`, the secret from step 1, and the events the
   `ticks` name plus `issues` and `issue_comment` for the router.

Its log is `journalctl --user -u widget-receiver`.
[forge-receiver](tools/forge-receiver.md) has the details.

## 8. The relay

The router wakes an agent by posting to an XMPP webhook relay on the
build host, which sends the agent's session a chat message.

* Install xmpp-mcp on the build host and run its `xmpp-webhook-relay`
  as a user service (its documentation says how), listening on
  `relay.url` (default `http://127.0.0.1:8788`).
* Put its shared token in `relay.token_file` (default
  `~/.config/xmpp-webhook-relay/token`), mode 0600, as an environment
  file with a `WEBHOOK_TOKEN=` line.
* Each agent's session joins the relay's room under its session name
  (`WidgetLeadDev`, step 3): the relay delivers a post to
  `/agent/<session name>` to the session that holds that name.

[forge-router](tools/forge-router.md#the-relay) has the details.

## 9. Agent sessions

Each agent is a Claude Code session, kept running by
claude-agent-supervisor, with the xmpp-mcp channel so that the
router's wakes arrive as messages. On each agent host:

* **Settings.** Set `FORGE_CRON_CHECKOUT` in the sessions'
  environment (the `env` block of Claude Code's settings) to a
  checkout kept at the default branch. The identity tools then read
  the roster from there, so a branch can't change whose App it gets.
  On a host that runs no other job, keep that checkout current with
  one cron line that runs a cheap tool through `forge-cron-run`, which
  resets it to the default branch each time, such as
  `*/5 * * * * FORGE_CRON_CHECKOUT=... .../forge-cron-run forge-config check`.
* **Who it is.** Set `FORGE_AGENT` to the agent's name in each
  session's own environment (the supervisor's per-agent settings).
  The signing tools take the agent's name from it and never guess.
* **Identity.** In each worktree it creates, an agent runs
  `forge-worktree-identity --push app <Name>` before its first commit.
  That stamps the worktree's git identity as the agent's bot and makes
  git push with the agent's App token
  ([forge-worktree-identity](tools/forge-worktree-identity.md),
  [forge-git-credential](tools/forge-git-credential.md)).
* **GitHub writes.** Comments and pull request bodies go through
  `forge-gh-signed`, which runs `gh` as the agent's App. The body must
  end with a line `— <FORGE_AGENT>` of its own, and an @-mention
  outside code is refused; `forge-new-body NAME < text` writes such a
  file and prints its path
  ([forge-gh-signed](tools/forge-gh-signed.md),
  [forge-new-body](tools/forge-new-body.md)).
* **Restarts.** `forge-session-start`, as a SessionStart hook, tells a
  restarted session what it owns and what died. Track a wrapper and
  the settings that name it in the repository, and install both with
  `forge-host-settings` (`host_settings.files`):

  ```json
  {
    "env": {"FORGE_CRON_CHECKOUT": "/home/agent/widget-cron"},
    "hooks": {"SessionStart": [
      {"matcher": "startup|resume",
       "hooks": [{"type": "command",
                  "command": "~/.claude/hooks/session-start"}]}]}
  }
  ```

  [forge-session-start](tools/forge-session-start.md) has the wrapper.
* **Roles.** Write `docs/roles.md` (`docs.roles`), one `## <Name>`
  section per agent saying what the agent does and owns; the hook
  gives each restarted session its own section.

Tell each agent, in its role brief, the working rules: one issue per
piece of work with an `Owner:` label; a pull request says `Fixes #N`;
put `Status:waiting-owner` on an issue when it needs you, and name the
pull request and head commit there; never merge, since the Merger does.

## 10. A day in the life

1. An agent opens a pull request. The receiver ticks the build check,
   which posts `pending` and starts a run in a free slot.
2. The router wakes the reviewer whose globs cover the change. The
   reviewer approves, or requests changes, which wakes the author.
3. The build check posts green. The Merger sees an approved, green,
   up-to-date head and puts it in a train with others that touch
   different files. Once the train's top is green, it merges each
   member in turn with no run between.
4. A pull request that conflicts with the default branch, or whose
   check is red, is handed back to its author with a comment that says
   why. The author pushes a fix, and it queues again.
5. Anything that needs you carries `Status:waiting-owner`.

[forge-merger](tools/forge-merger.md) describes every hand-back and
hold, and [forge-flow](tools/forge-flow.md) writes a page that says
why merging is slow when it is.

## 11. More, when you want it

| Want | Use |
| --- | --- |
| A red nightly treated as a red default branch | [forge-nightly](tools/forge-nightly.md), the `nightly` section |
| An issue when merging slows down | [forge-stats-watch](tools/forge-stats-watch.md), `stats_watch`; [forge-stats](tools/forge-stats.md) by hand |
| A page that says why merging is slow | [forge-flow](tools/forge-flow.md) |
| Agents' memories shared between hosts | [forge-memory-sync](tools/forge-memory-sync.md), [memory-sync.md](memory-sync.md) |
| Quiet on a host while something is timed | [forge-quiet](tools/forge-quiet.md), `quiet` |
| Slow jobs queued, with a message when done | [forge-queue](tools/forge-queue.md), `queue` |
| A second build host (another OS) | [`checks.instances`](config.md#checksinstances) |
| Your own rules for whose turn an item is | [router.md](router.md), `router.rules` |
| Decisions and design approvals checked | [forge-decision](tools/forge-decision.md), [forge-approval-scope](tools/forge-approval-scope.md) |
| Markdown checked in CI | [forge-doc-format](tools/forge-doc-format.md), [forge-doc-width](tools/forge-doc-width.md), [forge-doc-links](tools/forge-doc-links.md) |
| Issues closed only by the pull request meant to | [forge-closing-keywords](tools/forge-closing-keywords.md) as a `merge.gate` |

## When something is wrong

* `forge-config check` first: every problem is listed at once.
* Each job's log is `~/cron-logs/<tool>.log`; the receiver's is its
  unit's journal.
* `forge-merger --dry-run --checkout ~/widget-cron` says what the
  Merger would do and why, and writes nothing.
* `forge-router --checkout ~/widget-cron` (without `--send`) prints
  every decision and every message it would send.
* State is under `~/.local/state/agent-forge/widget/<tool>/`
  ([Where state lives](config.md#where-state-lives)).
