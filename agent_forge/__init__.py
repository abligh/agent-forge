"""agent-forge's shared library: what every tool needs and none should
copy. Standard library only.

    config   the project's .agent-forge/config.json, found and checked
    proc     running a command, with a timeout that never raises
    locks    exclusive locks and the state directory each tool owns
    state    JSON state files written whole
    markers  the lines agents write that tools act on (Not-a-review,
             Blocked-by:, Defer-until:)
"""

__version__ = "0.1.0"
