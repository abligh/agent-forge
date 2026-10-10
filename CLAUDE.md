# agent-forge

Tooling shared by projects that are developed by teams of AI agents on
GitHub. [docs/design.md](docs/design.md) says what belongs here and the
order of work; [docs/config.md](docs/config.md) is the configuration.

## Rules

* **Name no project, host or organisation** in any file, path or commit
  message. Projects are described by their own configuration;
  examples use `widget` and `example.org`. `tools/check-neutral`
  enforces this with a word list kept outside the repository; install
  it as the pre-commit hook (README).
* **Standard library Python only** (3.11+). A host installs agent-forge
  by cloning it.
* **Every tool in `bin/` is `forge-<name>`**, has a docstring whose first
  line says what it does, and has `tests/test_forge_<name>.py`. Every
  module in `agent_forge/` has `tests/test_<module>.py`.
  `tests/test_meta.py` checks all of this.
* **Tools never find the project from their own location**: they use
  `agent_forge.config`. Environment variables start `FORGE_` and only
  override configuration.
* **Anything that shells out takes `run`**, defaulting to
  `agent_forge.proc.sh`, so tests can fake it.
* **No tool merges or approves on its own judgment.** The Merger merges
  what reviewers approved, and nothing else does either.

## Running the checks

    make            # the tests and the neutrality check
    make test
