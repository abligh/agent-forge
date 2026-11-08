# forge-session-start

`forge-session-start` is a Claude Code SessionStart hook that tells a
session, before its first turn, what a restart killed and what the
agent owns. When every session on a host is restarted at once, every
background watch dies with them. A rule the agent has to remember at
that moment doesn't hold; a reminder put in front of it does. The brief
is a fixed checklist, the agent's open issues and pull requests, and
the agent's own section of the project's roles file.

## Usage

    forge-session-start < hook-input.json

It takes no arguments. Claude Code runs it and passes the hook's input
as JSON on stdin. Install it through a thin wrapper the project tracks,
for example `tools/host/session-start-hook`:

    #!/bin/sh
    exec forge-session-start

and name the wrapper in the settings the project tracks, such as
`tools/host/claude-settings.json`:

```json
{
  "env": {"FORGE_CRON_CHECKOUT": "/home/agent/widget-cron"},
  "hooks": {
    "SessionStart": [
      {"matcher": "startup|resume",
       "hooks": [{"type": "command",
                  "command": "~/.claude/hooks/session-start"}]}
    ]
  }
}
```

`forge-host-settings` installs both on every host
(`host_settings.files`). To see the brief by hand, from an agent's
worktree:

    echo '{"source": "startup"}' | forge-session-start

## Options

None. Everything comes from stdin, the environment and the
configuration. The hook input fields it reads:

| Field | Default | Meaning |
| --- | --- | --- |
| `source` | `startup` | Why the session started. Only `startup` and `resume` get a brief. |
| `cwd` | the working directory | The session's worktree, which says who the agent is. |

Input that isn't a JSON object is treated as `{}`, a startup.

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The cron checkout. When set, the configuration and the roles file are read from it, at the default branch, rather than from the worktree's branch. Set it in the settings' `env`. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Where else to find the configuration ([config.md, Finding it](../config.md#finding-it)). `FORGE_CONFIG` wins over `FORGE_CRON_CHECKOUT`. |
| `FORGE_SESSION_START_TIMEOUT` | Seconds the GitHub listing may take. Default 15. |
| `GH_TOKEN` | The token for the listing when the agent's App token can't be minted. Without either the call is anonymous. |
| `FORGE_GITHUB_API` | The GitHub API base. Default `https://api.github.com`. |

The tool sets `FORGE_AGENT` to the agent's name itself, to mint that
agent's App token; a value already in the environment is not used.

## Configuration

From `.agent-forge/config.json` ([config.md, Keys](../config.md#keys)):

* `repo`: where the items are listed.
* `agents`, `key_dir` and the agent's App fields: to mint the agent's
  App token for the listing.
* `labels.owner_prefix` (default `Owner:`) and `labels.status_prefix`
  (default `Status:`).
* `session_start.skip_labels`: items with any of these labels are left
  out of the listing.
* `docs.roles` (default `docs/roles.md`; see the `docs` section of
  [config.md](../config.md)): the roles file.
* `identity.unattributed_name` (default `Anonymous Agent`): a worktree
  stamped with this name counts as unstamped.

## What it does

**Only on `startup` and `resume`.** A compaction keeps the process, and
with it every background task, so a brief after one would wrongly say
the agent's watches were dead. The tool checks the source itself, so a
mis-set matcher can't make it lie: any other source prints nothing and
exits 0.

**Who the agent is.** The agent is the worktree's stamped identity,
`git config --worktree user.name`, as `forge-worktree-identity` writes
it. It is read only when `extensions.worktreeConfig` is on, since
without it git reads `--worktree` as the repository-wide setting. A
name set for the whole repository is therefore not a stamp, and nor is
the unattributed name.

**The brief.** It prints one JSON object on stdout:

```json
{"hookSpecificOutput": {"hookEventName": "SessionStart",
                        "additionalContext": "..."}}
```

`additionalContext` holds, in order:

1. `Session <source> at <UTC time> for <agent>.`
2. A fixed checklist: background tasks and monitors are gone; decide
   which open items need re-arming; check tools and scratch state
   rather than assuming.
3. The agent's open items: one REST call for the open issues and pull
   requests labelled `<owner_prefix><agent>`, at most 100. Each line
   is `#<number> PR|issue <status labels> <title>`. Items with a
   `session_start.skip_labels` label are left out, and the brief says
   how many, so the list never reads as complete when it isn't.
4. The agent's role: the body of the `## <agent>` section of the roles
   file. One section can brief several agents: `## LeadDev, LeadDev2`.
   The section ends at the next `## ` heading. An agent with no section
   is told so.

It lists, and doesn't re-arm. Only the session that owned a watch knows
whether it is still wanted, so the agent decides.

**When it can't list**, it says so rather than printing an empty list
that reads as "you own nothing":

* an unstamped worktree gets the checklist and the instruction to run
  `forge-worktree-identity <YourName>`, and no listing or role;
* no configuration found from `cwd`: the brief says so;
* a failed or timed-out listing: the brief gives GitHub's answer and
  the `gh api` command to run by hand.

## State and logs

It writes no files. It reads the configuration and the roles file,
from the cron checkout when `FORGE_CRON_CHECKOUT` is set. An unexpected
error is printed on stderr, which Claude Code shows in its hook output.

## Exit status

Always 0. A hook must never stop a session starting.

## See also

* [forge-host-settings](forge-host-settings.md), which installs the
  hook and the settings
* [config.md, Keys](../config.md#keys) (`session_start`, `labels`)
* [config.md, Finding it](../config.md#finding-it)
