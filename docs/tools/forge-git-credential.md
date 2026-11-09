# forge-git-credential

`forge-git-credential` is a git credential helper. When git pushes to
`https://github.com`, it answers with an installation token from the
GitHub App of the agent the worktree is stamped as. A push over SSH
with a host's deploy key is recorded by GitHub as the key owner's, and
so is every workflow run it starts. Pushing over HTTPS with this helper
makes each push, and each run it starts, the agent's own bot's.

## Usage

    forge-git-credential get|store|erase

git runs it; a person or an agent doesn't. The normal way to install
it is for one worktree, with
[forge-worktree-identity](forge-worktree-identity.md):

```sh
forge-worktree-identity --push app LeadDev
```

That writes these settings at worktree scope (`git config
--worktree`), where `<install>` is the agent-forge installation:

    remote.origin.pushurl = https://github.com/example/widget.git
    credential.https://github.com.helper =
    credential.https://github.com.helper = !<install>/bin/forge-git-credential
    credential.https://github.com.useHttpPath = false

The empty helper value comes first on purpose. It clears any helper a
broader scope set, such as the one `gh auth setup-git` writes to
`~/.gitconfig`. git asks every configured helper in turn and takes the
first answer, so without it another login could answer instead.
`remote.origin.url` is left alone, so fetching still goes over SSH.

To wire it by hand, the equivalent commands are:

```sh
git config extensions.worktreeConfig true
git config --worktree remote.origin.pushurl https://github.com/example/widget.git
git config --worktree --add credential.https://github.com.helper ''
git config --worktree --add credential.https://github.com.helper \
    '!/opt/agent-forge/bin/forge-git-credential'
git config --worktree credential.https://github.com.useHttpPath false
```

The worktree must also be stamped (`forge-worktree-identity LeadDev`),
since the stamp is how the helper knows which agent is pushing.

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `get` | | Answer a credential request (see below). |
| `store` | | Accepted and ignored: nothing is stored. Exits 0. |
| `erase` | | Accepted and ignored. Exits 0. |

Exactly one action is required. There are no other options. git
writes the request to standard input as `key=value` lines, ending at a
blank line or end of file (gitcredentials(7)).

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The configuration file itself. Wins over everything below. |
| `FORGE_CRON_CHECKOUT` | A checkout kept at the default branch, whose `.agent-forge/config.json` is read when `FORGE_CONFIG` is not set. Set it in agent sessions so a branch can't change the roster its push credential is judged by. See [Finding it](../config.md#finding-it). |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when neither of the above is set. |
| `FORGE_GITHUB_API` | The GitHub API base URL. Default `https://api.github.com`. |

With none of the first three set, the configuration is found from the
git checkout git is pushing from.

## Configuration

It reads the same keys as [forge-app-token](forge-app-token.md):
[`key_dir`, `naming`, `agents` and `machinery`](../config.md#keys),
and each agent's [App fields](../config.md#an-agent). The App setup it
needs is described on the [forge-app-token](forge-app-token.md#configuration)
page.

## What it does

On `get` with `protocol=https` and `host=github.com`, it:

1. reads the agent's name from `git config --worktree --get user.name`,
   the stamp `forge-worktree-identity` writes. Only worktree scope
   counts, and only when `extensions.worktreeConfig` is `true`. Without
   that extension, `--worktree` would read the repository's shared
   config, and a name there would stamp every worktree at once;
2. mints that agent's installation token, exactly as
   `forge-app-token --token` does, with the key and slug checked
   against GitHub;
3. writes to stdout:

       username=x-access-token
       password=<token>

**It never falls back.** If the worktree is not stamped, or the mint
fails, it answers nothing, says why on stderr and exits 1. git then
fails the push. For example:

    forge-git-credential: no push credential: this worktree has no user.name at worktree scope. Run forge-worktree-identity <Agent> here first; this helper does not fall back to another login.

**It stores nothing.** A token lasts an hour and a push takes seconds,
so each `get` mints a fresh token.

A `get` for any other host or protocol exits 0 with no answer, so git
can ask any other helper that is configured. The token is written only
to git, on stdout, and is never logged.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Answered, or nothing to answer (`store`, `erase`, or another host or protocol). |
| 1 | No credential: the worktree is unstamped, or the mint failed. |
| 2 | Usage: no action, more than one argument, or an unknown action. |

## See also

* [forge-worktree-identity](forge-worktree-identity.md): stamps the
  worktree and installs this helper.
* [forge-app-token](forge-app-token.md): mints the token, and checks
  the Apps.
* gitcredentials(7): git's credential-helper protocol.
