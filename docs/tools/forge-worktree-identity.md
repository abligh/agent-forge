# forge-worktree-identity

`forge-worktree-identity` stamps one git worktree so its commits are
authored as an agent's GitHub App bot, and verifies the stamp. Every
agent session on a host shares one `~/.gitconfig`, so without a stamp
every commit carries the host's identity and is attributed to no one
(`forge-commit-identity` refuses those). With `--push app` it also makes
the worktree push over HTTPS as the agent's App, instead of over SSH
with a deploy key that GitHub attributes to the key's owner.

## Usage

    forge-worktree-identity AGENT               stamp, then verify
    forge-worktree-identity --check AGENT       verify only
    forge-worktree-identity --push app AGENT    stamp, and push as the App
    forge-worktree-identity --push app --helper COMMAND AGENT

An agent runs it in its session, inside each worktree it creates,
before its first commit:

```sh
git worktree add ../widget-fix-12 -b fix-12
cd ../widget-fix-12
forge-worktree-identity --push app LeadDev
```

It acts on the worktree around the working directory. Running it again
changes nothing. A session-start hook or a check script can run
`--check` to prove a worktree is stamped without changing it.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `AGENT` | required | The agent to stamp as: a name under `agents` in the configuration. Machinery Apps are not accepted. |
| `--check` | off | Verify only; write nothing. Exits 1 if the worktree's identity (or, with `--push app`, its push settings) is not the agent's. |
| `--push app` | none | Also set this worktree to push as the agent's App, over HTTPS, through [forge-git-credential](forge-git-credential.md). `app` is the only value. |
| `--helper COMMAND` | this copy's | With `--push app`, the credential helper to store, written as git takes it (such as `!/opt/tools/git-credential`). Use it when agent-forge runs from a path that changes with each release, such as a pinned cache, so worktrees keep a helper that still exists. `--check` compares against it too. |
| `-h`, `--help` | | Print the usage and exit. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The configuration file itself. Wins over everything below. |
| `FORGE_CRON_CHECKOUT` | A checkout kept at the default branch, whose `.agent-forge/config.json` is read when `FORGE_CONFIG` is not set, so an agent's branch can't change the roster. See [Finding it](../config.md#finding-it). |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when neither of the above is set. |
| `FORGE_GITHUB_API` | The GitHub API base URL. Default `https://api.github.com`. |

## Configuration

It reads [`repo`, `key_dir`, `naming` and `agents`](../config.md#keys)
and each agent's [App fields](../config.md#an-agent). The App setup is
described on the [forge-app-token](forge-app-token.md#configuration)
page. The bot login is the App's slug followed by `[bot]`, such as
`leaddevwidgetagent[bot]`.

## What it does

**First it resolves the bot.** It asks GitHub `GET /users/<slug>[bot]`
and needs an answer whose `type` is `Bot`. It asks with the agent's own
App token when one can be minted, else anonymously (the endpoint is
public). The `id` in that answer is the bot user's id. It is not the
App id; the two are unrelated numbers. If the agent is not in the
configuration, or GitHub doesn't say the login is a Bot, nothing is
written and it exits 2.

**Then it stamps.** Without `--check` it runs:

    git config extensions.worktreeConfig true
    git config --worktree user.name  <AGENT>
    git config --worktree user.email <bot-user-id>+<slug>[bot]@users.noreply.github.com

The email is what GitHub links a commit to the bot by; the name is only
what a person reads, so it stays the agent's name. For example,
`LeadDev <4242+leaddevwidgetagent[bot]@users.noreply.github.com>`.

**Then it verifies, against the expected identity.** It reads
`user.name` and `user.email` from this worktree's own config only, and
compares them with what it expects. A non-empty value is not enough:
`git worktree add` copies the creating worktree's `config.worktree`
into the new one, so an unstamped worktree commits as whoever created
it. With `extensions.worktreeConfig` off, the worktree has no identity
of its own and verification fails. On success it prints:

    forge-worktree-identity: this worktree commits as LeadDev <4242+leaddevwidgetagent[bot]@users.noreply.github.com>

**With `--push app`**, after the identity passes, it writes at worktree
scope only:

    remote.origin.pushurl = https://github.com/<repo>.git
    credential.https://github.com.helper = (empty)
    credential.https://github.com.helper = !<install>/bin/forge-git-credential
    credential.https://github.com.useHttpPath = false

`<install>` is the directory of the agent-forge copy this tool runs
from, which outlives any worktree. `--helper` stores another command in
its place. The empty helper comes first to
clear a helper set at a broader scope, such as by `gh auth setup-git`.
A key that already holds exactly these values is left alone; one that
holds anything else is replaced, not added to. `remote.origin.url` is
unchanged, so fetching still uses SSH. It then reads the values back
and prints:

    forge-worktree-identity: this worktree pushes as leaddevwidgetagent[bot] over HTTPS

To undo the push settings for one worktree:

```sh
git config --worktree --unset remote.origin.pushurl
```

Other worktrees of the same repository are never changed.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | The worktree's identity (and with `--push app`, its push credential) is the agent's. |
| 1 | It is not (with `--check`), or a `git config` write failed or did not read back. |
| 2 | The configuration could not be read, the agent is not in it, its bot could not be resolved as a Bot user, or a usage error. |

## See also

* [forge-commit-identity](forge-commit-identity.md): refuses commits
  made without a stamp.
* [forge-git-credential](forge-git-credential.md): the push credential
  `--push app` installs.
* [forge-app-token](forge-app-token.md): the App setup.
