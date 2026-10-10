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
| `identity` | no | The host's unconfigured git identity, which no commit may carry: `unattributed_domain` (an email domain such as `@host.example.org`; default `$FORGE_UNATTRIBUTED_DOMAIN`, else the host's own fully qualified name, so a configuration need not name its hosts) and `unattributed_name`. |
| `merge` | no | How `forge-merger` merges: `merger`, the machinery App that acts (default `Merger`); `ci_workflow`, the Actions workflow every head must pass (default `ci`; `""` for none); `gate`, tools that must exit 0 before a merge, each an argument list with `{pr}`, `{base}` and `{head}` filled in, a `forge-` name run from the installation and anything else from the cron checkout (default `[["forge-commit-identity", "{base}..{head}"]]`); `stall_minutes`, how long a head may wait on its checks before the tick reports it stalled (default 30). The required status is `checks.context`, counted only when `checks.poster`'s App (default the merger) posted it. |
| `host_settings` | no | Files every agent host carries, installed by `forge-host-settings`: `files`, each `{"from": path in the project, "to": path under the home directory, "mode": "0644", "json": true when compared as JSON}`, and `host_keys`, keys a JSON file's host may set for itself. |
| `receiver` | no | How `forge-receiver` takes GitHub's webhook deliveries: `address` and `port` it listens on (default `127.0.0.1`, 8789), `path` (default `/hook`), `secret_file`, the webhook's secret, mode 0600 (default `~/.config/agent-forge/<project>/receiver-secret`), and `max_body`, the largest body in bytes (default 1048576); `ticks`, which tools each event runs, by GitHub event name (`issues`, `issue_comment`, `pull_request`, `pull_request_review`, `status`, `push`), each tool a name or an argument list, a `forge-` name run from the installation and anything else from the cron checkout (default none; `bin/forge-receiver` says when each event counts); and `route`, the router's argument list with `{items}` filled in as `N[,N]`, run for the items events name (default none: nothing is routed). |
| `docs` | no | The project's documentation, as the tools that read it see it; see below. |
| `checks` | no | The build check, `forge-pr-check`; see below. |
| `labels` | no | The labels tools read: `owner_prefix` (default `Owner:`; `Owner:<Name>` says whose an item is), `status_prefix` (default `Status:`), `waiting_on_owner` (default `Status:waiting-owner`: the owner's turn) and `waiting_external` (default `Status:waiting-external`: nobody's). |
| `relay` | no | Where `forge-router` posts wakes: `url`, the xmpp-webhook-relay's base (default `http://127.0.0.1:8788`), `token_file`, its environment file holding `WEBHOOK_TOKEN` (default `~/.config/xmpp-webhook-relay/token`), `remind_seconds`, how long before a turn is woken again (default 7200), and `room`, the room the agents share. |
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
| `merger_first` | `false` | Check first the head of the pull request the Merger waits on (its `next.json`), and start nothing else while it names one. Right under branch protection that requires heads to be up to date, where any other verdict is spent by the next merge; it holds only while the Merger ticks at least every 10 minutes. |

Only these keys are checked; the section may hold others, for tools
not here yet.

## Where state lives

Each tool keeps its state in
`~/.local/state/agent-forge/<project>/<tool>/` (under
`$XDG_STATE_HOME` if set). `$FORGE_STATE_DIR` replaces
`~/.local/state/agent-forge` for tests and for hosts that keep state
elsewhere.
