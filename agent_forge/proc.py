"""Running commands. Every tool that shells out goes through `sh`, so a
test can hand a tool a fake with the same signature."""

import subprocess


def sh(args, cwd=None, env=None, timeout=None):
    """Run `args`, capturing text output. A timeout comes back as exit
    124, as timeout(1) reports one, rather than raising; a missing
    program comes back as 127, as a shell reports one."""
    try:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired as err:
        return subprocess.CompletedProcess(
            args, 124, _text(err.stdout),
            _text(err.stderr) + "timed out after %ss\n" % timeout)
    except FileNotFoundError as err:
        return subprocess.CompletedProcess(args, 127, "", "%s\n" % err)


def _text(value):
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) \
        else value
