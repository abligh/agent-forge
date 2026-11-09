# forge-app-token

`forge-app-token` mints a GitHub App installation token for one agent,
or for one machinery App such as the Merger's. Each agent has its own
GitHub App, so whatever it does with that token is recorded on GitHub
as the App's bot, not as a shared login or the owner's account. The
tool also checks every App in the configuration against GitHub, and
lists them without touching the network. Nothing is written to disk,
and no message on stderr ever contains a token.

## Usage

    forge-app-token --token NAME
    forge-app-token --check
    forge-app-token --list

Exactly one of the three options is required.

An agent runs `--token` in its session when it needs a token for a
command of its own, such as `gh` or `curl`:

```sh
GH_TOKEN=$(forge-app-token --token LeadDev) gh issue list
```

The token is the only thing on stdout, so command substitution is
safe. Other tools mint the same way through the shared library:
`forge-git-credential` for pushes and `forge-gh-signed` for `gh`
calls, so an agent rarely needs to call this tool for those.

A person setting up a host runs `--list` to see what the configuration
says, then `--check` to prove that every key and id agrees with GitHub.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--token NAME` | none | Mint and print an installation token for `NAME`, an agent or a machinery App in the configuration. |
| `--check` | off | For every agent and machinery App, check that the key signs for the App the configuration names and that its installation exists. Prints one line each. |
| `--list` | off | Print every agent and machinery App with its ids and key path. No network. |
| `-h`, `--help` | | Print the usage and exit. |

`--token`, `--check` and `--list` are mutually exclusive.

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The configuration file itself. Wins over everything below. |
| `FORGE_CRON_CHECKOUT` | A checkout kept at the default branch. When set (and `FORGE_CONFIG` is not), the configuration is read from its `.agent-forge/config.json`, so an agent's branch can't change the roster its token is judged by. See [Finding it](../config.md#finding-it). |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when neither of the above is set. |
| `FORGE_GITHUB_API` | The GitHub API base URL. Default `https://api.github.com`. |

With none of the first three set, the configuration is found from the
git checkout around the working directory.

## Configuration

It reads [`key_dir`, `naming`, `agents` and `machinery`](../config.md#keys),
and each entry's App fields, described under
[An agent](../config.md#an-agent).

The GitHub App setup it assumes, precisely:

* **One App per agent**, and one per machinery role (such as `Merger`).
  Each is installed on the project's `repo`.
* **`app_id`** is the App's numeric id and **`installation_id`** the id
  of its installation on `repo`. Both must be positive integers. While
  either is `null`, the App counts as not made yet: no token is minted
  and the key is not read (exit 6).
* **The private key** is a PEM file in `key_dir` (a leading `~` is
  expanded). Its name is `<name>.pem`, such as `LeadDev.pem`, unless the
  entry's `key` names another file in `key_dir`. With no `key_dir` in
  the configuration, nothing can be minted (exit 2). Keep the files
  mode 0600; the tool reads them with `openssl`, which must be on the
  path.
* **The slug** is the App's URL name on GitHub. It is the entry's
  `slug` if set, else the agent's name followed by
  `naming.app_slug_suffix`, lower-cased. With
  `"app_slug_suffix": "widgetagent"`, agent `LeadDev` expects the slug
  `leaddevwidgetagent`, and its bot login is
  `leaddevwidgetagent[bot]`. The configuration check refuses an entry
  with no `slug` when there is no suffix to make one.

An example entry, with the key at `~/agent-keys/widget/Helper.2026.pem`:

```json
{
  "key_dir": "~/agent-keys/widget",
  "naming": {"app_slug_suffix": "widgetagent"},
  "agents": {
    "LeadDev": {"app_id": 101, "installation_id": 201},
    "Helper": {"app_id": 104, "installation_id": 204,
               "slug": "helperbot", "key": "Helper.2026.pem"}
  },
  "machinery": {"Merger": {"app_id": 105, "installation_id": 205}}
}
```

## What it does

**`--token NAME`** looks `NAME` up among the agents, then the machinery
Apps. It checks that both ids are set, that `key_dir` is configured and
that the key file exists. It then signs a ten-minute JWT with the key
(RS256 by `openssl dgst -sha256 -sign`). The JWT's issue time is one
minute in the past, because GitHub refuses one from its future.

Before minting, it asks GitHub `GET /app` with that JWT, which tells
it which App the key belongs to. The answer's slug must equal the slug
the configuration expects. So a key filed under the wrong name is
refused, rather than letting one agent act as another. Then it asks for
an installation token with `POST /app/installations/<id>/access_tokens`
and prints the token alone. A token lasts an hour.

Each request has a 30-second timeout. A timeout or a network failure
counts as "could not reach GitHub" (exit 5).

**`--check`** does the same key and slug check for every agent and
machinery App, then reads `GET /app/installations/<id>`. It prints one
line per App, either:

    LeadDev           app 101 slug leaddevwidgetagent installation 201 on example

or the reason it failed, such as `LeadDev's App is not made yet:
app_id and installation_id is null in ...` or `installation 201
refused (404)`. It checks every App even after a failure.

**`--list`** prints, for every agent then every machinery App:

    LeadDev           app 101      installation 201        /home/u/agent-keys/widget/LeadDev.pem
    Merger            app 105      installation 205        /home/u/agent-keys/widget/Merger.pem  (machinery)

A `null` id prints as `null`. With no `key_dir` the path prints as
`None`.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Minted, listed, or every App checked good. |
| 1 | No agent or machinery App by that name. |
| 2 | The configuration or a key could not be read (including no `key_dir`, a missing key file, or `openssl` failing), or a usage error. |
| 3 | GitHub refused the App or its installation token, or the key belongs to another App. |
| 4 | `--check` found an App that disagrees with GitHub or isn't made. |
| 5 | GitHub could not be reached. |
| 6 | The App isn't made yet: `app_id` or `installation_id` is null. |

## See also

* [forge-git-credential](forge-git-credential.md): the same token as
  git's push credential.
* [forge-gh-signed](forge-gh-signed.md): runs `gh` as the agent's App.
* [forge-worktree-identity](forge-worktree-identity.md): stamps a
  worktree's commits with the App's bot identity.
* [Configuration](../config.md#an-agent): an agent's App fields.
