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
| `identity` | no | The host's unconfigured git identity, which no commit may carry: `unattributed_domain` (an email domain such as `@host.example.org`) and `unattributed_name`. |
| `labels`, `checks`, `docs`, `relay` | no | Sections each tool documents for itself, as it moves here. |

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

## Where state lives

Each tool keeps its state in
`~/.local/state/agent-forge/<project>/<tool>/` (under
`$XDG_STATE_HOME` if set). `$FORGE_STATE_DIR` replaces
`~/.local/state/agent-forge` for tests and for hosts that keep state
elsewhere.
