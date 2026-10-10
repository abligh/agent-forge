"""JSON state files, read forgivingly and written whole."""

import contextlib
import json
import os
import tempfile


def load(path, defaults=None):
    """The JSON object at `path`, or {} if it is missing or unreadable,
    with each key of `defaults` set where absent. A tool that can't read
    its state starts afresh rather than stopping."""
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key, value in (defaults or {}).items():
        data.setdefault(key, json.loads(json.dumps(value)))
    return data


def save(path, data):
    """Write `data` to a temporary file beside `path` and rename it into
    place, so a reader never sees half a file."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

