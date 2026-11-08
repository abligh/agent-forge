# forge-gh-signed

`forge-gh-signed` runs `gh` with a body file, as the agent's own GitHub
App, after checking the file carries the agent's sign-off and no
`@`-mention. The check happens where the call is made, so it holds
whatever produced the file. Running as the App means GitHub records the
agent's bot, not whatever login `gh` happens to hold, which may be
shared or a person's.

## Usage

    forge-gh-signed <gh arguments...>

The arguments are `gh`'s own, and must include `--body-file PATH` (or
`--body-file=PATH`), except for `pr ready`. An agent runs it in its
session wherever it would run `gh` with a body, normally on a file from
[forge-new-body](forge-new-body.md):

```sh
export FORGE_AGENT=LeadDev
forge-gh-signed issue comment 12 --body-file "$path"
forge-gh-signed pr create --title "Reject an empty header" --body-file "$path"
forge-gh-signed pr ready 34
```

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `<gh arguments...>` | | Passed to `gh` unchanged. |
| `--body-file PATH`, `--body-file=PATH` | required | The body file to check and send. If given more than once, the last one is checked. Not required for `pr ready`. |

Its own options are only these: everything else on the command line is
`gh`'s, and is not interpreted. With `pr ready` (and `pr ready --undo`),
no body file is needed, since that command takes no body; but any
argument starting `-b`, `--body` or `-F`, or `--fill`, makes the call
need a `--body-file` as any other does.

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_AGENT` | Required. The agent sending: a bare name of letters, digits, `_` or `-`. Its sign-off is checked, and its App's token is minted. |
| `FORGE_CONFIG` | The configuration file itself. Wins over everything below. |
| `FORGE_CRON_CHECKOUT` | A checkout kept at the default branch, whose `.agent-forge/config.json` is read when `FORGE_CONFIG` is not set, so an agent's branch can't change the roster its signature is judged by. See [Finding it](../config.md#finding-it). |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when neither of the above is set. |
| `FORGE_GITHUB_API` | The GitHub API base URL for minting. Default `https://api.github.com`. |
| `GH_TOKEN`, `GITHUB_TOKEN` | Not read. In `gh`'s environment, `GH_TOKEN` is replaced by the App token and `GITHUB_TOKEN` removed. |

## Configuration

It reads the same keys as [forge-app-token](forge-app-token.md), to
mint `FORGE_AGENT`'s token: [`key_dir`, `naming`, `agents` and
`machinery`](../config.md#keys), and each agent's
[App fields](../config.md#an-agent). The App setup is described on the
[forge-app-token](forge-app-token.md#configuration) page.

## What it does

In order:

1. **A body file is required.** A call without `--body-file` is
   refused (exit 2), whatever else would supply a body: `--body`,
   `--fill`, or a flag `gh` adds later. Checking for known-bad flags
   would go stale; requiring the one good one does not. A path that is
   not an existing file is exit 2 as well.
2. **The sign-off.** The file must hold a line that is exactly
   `— <FORGE_AGENT>` (U+2014, the em dash). A body signed by another
   agent, or unsigned, is exit 3.
3. **No `@`-mentions** outside code, by the same rule as
   [forge-new-body](forge-new-body.md#what-it-does). One is exit 5, and
   nothing is sent.
4. **The App token.** It mints `FORGE_AGENT`'s installation token as
   `forge-app-token --token` does. If that fails, it refuses (exit 4)
   rather than fall back to `gh`'s own login.
5. **It runs `gh`**, replacing its own process, with the arguments
   unchanged, `GH_TOKEN` set to the token and `GITHUB_TOKEN` removed.
   The token goes only into `gh`'s environment.

## Exit status

| Status | Meaning |
| --- | --- |
| 2 | No `--body-file`, the file does not exist, or `FORGE_AGENT` unset or not a bare name. |
| 3 | The file is not signed by `FORGE_AGENT`. |
| 4 | No App token could be minted for `FORGE_AGENT`. |
| 5 | The file holds an `@`-mention outside code. |
| other | Once `gh` runs, its own exit status. |

## See also

* [forge-new-body](forge-new-body.md): writes the signed file.
* [forge-app-token](forge-app-token.md): the token, and the App setup.
