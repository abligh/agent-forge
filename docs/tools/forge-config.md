# forge-config

`forge-config` checks and reads a project's agent-forge configuration,
`.agent-forge/config.json`. Every other tool loads the same file and
refuses to run when it has a problem, so a project checks it before
committing a change to it. Scripts and crontabs read values from it
here rather than parsing the JSON themselves.

## Usage

    forge-config check
    forge-config get KEY
    forge-config agents [--kind developer|reviewer|collaborator]
    forge-config path

Run it by hand, or in CI, from the project's checkout:

    cd ~/widget && forge-config check

In a script:

    merger=$(forge-config get merge.merger)
    forge-config agents --kind reviewer | cut -f1

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `check` | | Check the whole file and list every problem. |
| `get KEY` | | Print one value by its dotted path, such as `agents.Reviewer.kind` or `checks.slots`. A string prints bare; anything else prints as JSON, keys sorted. |
| `agents` | | Print one agent per line, in the file's order: name, kind, App slug and session name, separated by tabs. |
| `--kind KIND` | every kind | For `agents`: only agents of this kind, `developer`, `reviewer` or `collaborator`. |
| `path` | | Print where the file is, or would be. It doesn't read or check the file. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The file itself. |
| `FORGE_PROJECT_ROOT` | Else, the checkout whose `.agent-forge/config.json` is read. |

Without either, it is `.agent-forge/config.json` at the top of the git
checkout around the working directory
([config.md, Finding it](../config.md#finding-it)).
`FORGE_CRON_CHECKOUT` is not consulted.

## Configuration

It reads the whole file and checks it as every tool does
([config.md, Checking](../config.md#checking)): every problem is
reported at once, and a key agent-forge doesn't know is a problem. Keys
that start with `_` are comments.

`get` reads values as written in the file. It doesn't fill in defaults:
a key the file leaves out is absent, even if it has a default.

## What it does

* `check` prints
  `forge-config: <path> is good: project <name>, <repo>, <n> agent(s),
  <m> reviewer(s)`, or `<path> has <n> problem(s):` on stderr, then
  each problem indented.
* `get` prints the value, or `no '<KEY>' in <path>` on stderr.
* `agents` prints, for each agent, `<name>\t<kind>\t<slug>\t<session>`.
  The kind defaults to `developer`. The slug is the agent's `slug`,
  else its name followed by `naming.app_slug_suffix`, in lower case.
  The session name is its `session_name`, else
  `naming.session_prefix` followed by its name.
* `path` prints the path.

Every subcommand but `path` checks the whole file first, so `get` and
`agents` fail on any problem anywhere in it.

## State and logs

It reads only; it writes nothing.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | The file has problems, or `get`'s key is absent. |
| 2 | There is no file to read: none found, or not in a git checkout and neither variable set. |

## See also

* [config.md](../config.md), every key
* [examples/config.json](../../examples/config.json), a complete
  example
