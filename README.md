# agent-forge

Tooling for a software project developed by a team of AI agents on
GitHub: a Merger that merges what reviewers approved, a build check
that tests every pull request's head, a webhook receiver, a router that
wakes whichever agent's turn it is, per-agent App identities, and the
small tools around them.

New to agent-forge? [docs/getting-started.md](docs/getting-started.md)
takes a project from nothing to a working team: the GitHub Apps, the
configuration, branch protection, the crontab, webhooks and the agents'
sessions. [docs/config.md](docs/config.md) explains every configuration
key, and each tool has a page under [docs/tools/](docs/tools/) with
every option it takes.

* Python 3.11+, standard library only.
* A project describes itself in one file, `.agent-forge/config.json`,
  in its own repository; nothing project-specific lives here.
* Installed per host, at a pinned version.

Companions: [claude-agent-supervisor](https://github.com/abligh/claude-agent-supervisor)
keeps each agent's Claude Code session running, and
[xmpp-mcp](https://github.com/abligh/xmpp-mcp) is the chat channel the
router wakes agents through.

## Layout

    agent_forge/   the shared library: config, proc, locks, state, markers
    bin/           the tools, each forge-<name>
    docs/          the design and the configuration reference
    examples/      an example project configuration
    tests/         one test file per tool and module, and meta-tests
    tools/         this repository's own checks

## Tools

The merge pipeline, which runs from cron and on webhooks:

| Tool | What it does |
| --- | --- |
| [`forge-merger`](docs/tools/forge-merger.md) | Merges each reviewer-approved pull request in turn, oldest first. |
| [`forge-pr-check`](docs/tools/forge-pr-check.md) | Builds and tests each open pull request's head on the host, under a memory limit, and posts the status the Merger requires. |
| [`forge-router`](docs/tools/forge-router.md) | Wakes each agent whose turn an open issue or pull request is, through the XMPP relay. |
| [`forge-receiver`](docs/tools/forge-receiver.md) | Takes GitHub's webhook deliveries and ticks the tools an event gives work. |
| [`forge-cron-run`](docs/tools/forge-cron-run.md) | Runs a project's tool from cron against the reviewed checkout, under locks. |

Identity, signing and decisions, which agents run:

| Tool | What it does |
| --- | --- |
| [`forge-app-token`](docs/tools/forge-app-token.md) | Mints an installation token for one agent's GitHub App. |
| [`forge-git-credential`](docs/tools/forge-git-credential.md) | Gives git the stamped agent's App token as its push credential. |
| [`forge-worktree-identity`](docs/tools/forge-worktree-identity.md) | Stamps a worktree's git identity as an agent's bot, and verifies it. |
| [`forge-commit-identity`](docs/tools/forge-commit-identity.md) | Refuses commits made under the host's unconfigured identity. |
| [`forge-new-body`](docs/tools/forge-new-body.md), [`forge-gh-signed`](docs/tools/forge-gh-signed.md) | Write a signed body file, and run `gh` with it as the agent's App. |
| [`forge-decision`](docs/tools/forge-decision.md) | Says whether the owner decided something, on this item, after it was asked. |
| [`forge-approval-scope`](docs/tools/forge-approval-scope.md) | Says whether a branch adds text to an approved design without saying so. |
| [`forge-closing-keywords`](docs/tools/forge-closing-keywords.md) | Reports every issue a squash merge will close, refusing a negated one. |
| [`forge-resolve`](docs/tools/forge-resolve.md) | Merges the default branch in as the Merger would, keeping both sides of a conflict of additions. |

Documentation and hosts:

| Tool | What it does |
| --- | --- |
| [`forge-doc-format`](docs/tools/forge-doc-format.md), [`forge-doc-width`](docs/tools/forge-doc-width.md), [`forge-doc-links`](docs/tools/forge-doc-links.md) | Check Markdown: whitespace and headings, line width, links and anchors. |
| [`forge-install`](docs/tools/forge-install.md) | Moves this installation to the commit the project pins, between jobs. |
| [`forge-stats`](docs/tools/forge-stats.md) | Reports how fast work lands: check waits and slot use, the merge queue, approval to merge per author; with `--actions`, the GitHub Actions minutes of the window's runs, without the billing API. |
| [`forge-stats-watch`](docs/tools/forge-stats-watch.md) | Runs hourly from cron and compares `forge-stats`' last hours with the week before and with limits; opens an issue for `stats_watch.owner` when a figure is out of line, comments while it stays out, and closes it once back in line ([designs/0024](docs/designs/0024-a-watch-on-how-fast-work-lands.md)). |
| [`forge-flow`](docs/tools/forge-flow.md) | Runs every five minutes from cron and writes a static page, with its JSON, that says why merging is slow: each hour's Merger time by state (merging, waiting on a batch top or a head check, culprit search, handed back, held, nothing eligible), flow, runs per merge, pull requests per merge slot, hand-backs, latency, slot-hours and policy ([designs/0045](docs/designs/0045-a-page-that-says-why-merging-is-slow.md)). |
| [`forge-nightly`](docs/tools/forge-nightly.md) | Runs every five minutes from cron and treats a red nightly as a red main: one issue while it is red, owned by whoever owns the failed steps; the pull requests that close it go first in the build check, the Merger and the router; the nightly is run again by itself (with the failed steps first, when the workflow takes `first`) until it is green; and the developers, then the owner, are told if it stays red ([designs/0028](docs/designs/0028-a-red-nightly-is-a-red-main.md)). |
| [`forge-host-settings`](docs/tools/forge-host-settings.md) | Compares a host's agent settings and crontab with the project's tracked copies, or installs them; `--apply`, from cron, keeps them installed. |
| [`forge-session-start`](docs/tools/forge-session-start.md) | A SessionStart hook: tells a restarted session what died and what it owns. |
| [`forge-memory-sync`](docs/tools/forge-memory-sync.md), [`forge-memory-check`](docs/tools/forge-memory-check.md), [`forge-memory-trim`](docs/tools/forge-memory-trim.md) | Share the agents' memory between hosts through a git repository; check the index still loads in full; shorten its lines safely ([docs/memory-sync.md](docs/memory-sync.md)). |
| [`forge-quiet`](docs/tools/forge-quiet.md) | Holds a shared host quiet while someone times something on it, and tells a job whether it must wait: `hold <minutes> <reason>`, `release`, `status [--wait M]`. A hold is a file in `quiet.dir` that ends by itself; `forge-pr-check` starts nothing while one is live. |
| [`forge-queue`](docs/tools/forge-queue.md) | Queues slow work (a gate run, a timing) in its own systemd user unit, frozen at a commit, and tells the agent through the relay when it ends: `submit QUEUE -- COMMAND`, `list`, `status`, `cancel`, `recover` ([designs/0023](docs/designs/0023-a-queue-that-tells-you-when-your-job-is-done.md)). |
| [`forge-prune-builds`](docs/tools/forge-prune-builds.md) | Clears the build output (tagged cache directories) of worktrees nobody is using, sooner once their pull request has merged ([designs/0016](docs/designs/0016-clear-build-output-of-idle-worktrees.md)), and, with `--high-water`, whatever is least recently used while the disk is too full, shared target directories included ([designs/0041](docs/designs/0041-clear-build-output-before-the-disk-fills.md)); with `--aged-cache DIR`, the files of an untagged cache such as GOCACHE unused for `--aged-days` (default 5). |
| [`forge-config`](docs/tools/forge-config.md) | Checks and reads the project's configuration. |

## Installing

Clone at a release tag and put `bin/` on the path; nothing else is
needed. Each project then adds `.agent-forge/config.json`
([docs/config.md](docs/config.md)) and checks it:

```sh
git clone --branch vX.Y.Z https://github.com/abligh/agent-forge ~/.local/share/agent-forge
ln -s ~/.local/share/agent-forge/bin/forge-* ~/.local/bin/
cd ~/my-project && forge-config check
```

To hold a host to a version, the project commits `.agent-forge/pin`:
the full commit SHA it approved, optionally followed by the tag.
`forge-cron-run` then runs nothing while the installation's `HEAD` is
any other commit ([docs/config.md](docs/config.md#the-pin)). A tag can
be moved; a commit can't. `forge-install`, run from cron every few
minutes, moves the installation to the pin once it changes. With
`--versions` it installs the new release beside the running one and
switches a symlink, so no job is waited for and none is held off:

```sh
* * * * * FORGE_CRON_CHECKOUT=$HOME/my-project-cron $HOME/.local/share/agent-forge/bin/forge-install --versions
```

Without it, the installation is moved in place, between jobs.

## Tests

```sh
make
```

## Neutrality

`tools/check-neutral` refuses any tracked file that names a word from a
list kept outside the repository (`$FORGE_FORBIDDEN_WORDS`, default
`~/.config/agent-forge/forbidden-words`). Install it as a pre-commit
hook:

```sh
printf '#!/bin/sh\nexec tools/check-neutral --staged\n' > .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## License

MIT; see [LICENSE](LICENSE).
