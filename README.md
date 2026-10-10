# agent-forge

Tooling for a software project developed by a team of AI agents on
GitHub: a Merger that merges what reviewers approved, a build check
that tests every pull request's head, a webhook receiver, a router that
wakes whichever agent's turn it is, per-agent App identities, and the
small tools around them.

Status: **being assembled.** The skeleton is here: configuration,
library and tests. The tools still exist as separate copies inside the
projects that use them, and move here one at a time;
[docs/design.md](docs/design.md) says what moves, what each copy
contributes, and in what order.

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

## Installing

Clone at a release tag and put `bin/` on the path; nothing else is
needed. Each project then adds `.agent-forge/config.json`
([docs/config.md](docs/config.md)) and checks it:

```sh
git clone --branch vX.Y.Z https://github.com/abligh/agent-forge ~/.local/share/agent-forge
ln -s ~/.local/share/agent-forge/bin/forge-* ~/.local/bin/
cd ~/my-project && forge-config check
```

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
