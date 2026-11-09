# forge-commit-identity

`forge-commit-identity` refuses a range of commits if any of them was
authored or committed under the host's unconfigured git identity, or
under an agent's name without that agent's stamped address. Such a
commit came from a worktree nobody stamped with
[forge-worktree-identity](forge-worktree-identity.md). GitHub links a
commit to an account by its email, and the host's default address
belongs to no account, so the commit is attributed to no one. A project
runs this before merging so that never lands.

## Usage

    forge-commit-identity BASE..HEAD

It is the Merger's default gate. With no `merge.gate` in the
configuration, `forge-merger` runs it on every pull request as if the
configuration said:

```json
"merge": {"gate": [["forge-commit-identity", "{base}..{head}"]]}
```

`{base}` is the default branch's tip and `{head}` the pull request's
head. Setting `merge.gate` replaces that default, so a project that sets
it lists this entry too to keep the check. An agent can run it before pushing:

```sh
forge-commit-identity origin/main..HEAD
```

It can also run in CI or as a check step, on the range the change adds.
It reads the range from the git repository around the working
directory.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `range` | required | The commits to judge, in any form `git log` accepts, normally `BASE..HEAD`. |
| `-h`, `--help` | | Print the usage and exit. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_UNATTRIBUTED_DOMAIN` | Reserved email domains, comma-separated, such as `@build.example.org,@build`. Used only when the configuration has no `identity.unattributed_domain`. |
| `FORGE_CONFIG` | The configuration file itself. |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when `FORGE_CONFIG` is not set. |

With neither of the last two set, the configuration is found from the
git checkout around the working directory. Unlike the App-token tools,
it does not read `FORGE_CRON_CHECKOUT`: as a Merger gate it already
runs in the cron checkout.

## Configuration

It reads [`identity`](../config.md#keys) (its `unattributed_domain` and
`unattributed_name`), [`agents`](../config.md#an-agent) and
[`naming`](../config.md#keys), the last two to work out each agent's bot
address. It needs no App keys and makes no network calls.

`identity.unattributed_domain` is one domain or a list, each starting
with `@`, such as `"@build.example.org"`. When set, it replaces the
defaults below.

## What it does

It lists every commit in the range with `git log`, leaving out only
the merge commit GitHub makes for a `pull_request` checkout (subject
`Merge <sha> into <sha>`), which no squash carries. A merge an agent
made on its own branch is judged like any other commit. For each
commit it judges both the author and the committer.

An identity is refused when:

1. **its email is on a reserved domain**, whatever the local part
   says. The domains are, in order of precedence:
   `identity.unattributed_domain`; else `$FORGE_UNATTRIBUTED_DOMAIN`;
   else the host's own names. Those are its fully qualified name, its
   `hostname`, and every leading run of their labels, since git's
   default email may use any of them. A host named
   `build.example.org` reserves `@build`, `@build.example` and
   `@build.example.org`. The match is on the end of the email, case
   insensitive;
2. **its name is `identity.unattributed_name`**, in any case, whatever
   the email;
3. **its name claims an agent without that agent's stamp.** When the
   name's leading word (letters and digits) is exactly an agent's name,
   case insensitive, the email must be the agent's bot address,
   `<digits>+<slug>[bot]@users.noreply.github.com`, and nothing else.
   So `Reviewer (scratch) <reviewer@localhost>` is refused, and
   `Reviewer2` claims an agent `Reviewer2`, not `Reviewer`. A person's
   name, or an agent's bot login, is not judged by this rule. This
   catches a `git config user.name` written without `--worktree`, which
   lands in the shared repository config.

Each refusal goes to stderr, one per identity:

    1a2b3c4d5e6f author is claude <claude@build.example.org>, the unconfigured host identity; it is attributed to no one

then a remedy: stamp the worktree, and re-author the commits onto a new
branch with `git rebase --exec 'git commit --amend --no-edit
--reset-author' <base>` and a new pull request, rather than
force-pushing a reviewed one. Last, on stdout:

    forge-commit-identity: 3 commit(s) examined, 1 unattributed

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Every commit in the range is attributable. |
| 1 | At least one is not; each is listed, with the remedy. |
| 2 | The configuration or the range could not be read, the range held no commits other than GitHub's synthetic merge (a range that examined nothing must not read as clean), or a usage error. |

## See also

* [forge-worktree-identity](forge-worktree-identity.md): the stamp
  this checks for.
* [Configuration keys](../config.md#keys): `identity` and `merge.gate`.
