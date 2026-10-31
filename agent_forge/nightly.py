"""What forge-nightly (designs/0028) tells the other tools: the pull
requests that fix a red nightly, and which issue says it is red.

forge-nightly writes NIGHTLY_FILE in its state directory on every
tick. While its issue saying the nightly is red is open, `fixes` there
lists the open pull requests that close the issue, by the rule
forge-closing-keywords applies: a closing keyword in the title, the
body or a commit message, or the issue in `closingIssuesReferences`.
forge-pr-check, forge-merger and forge-router treat each of them as if
it carried `labels.urgent`. With `router.red_first` (designs/0039), the
router's default rules read the issue (`current`) too.

They read the file rather than ask GitHub: forge-nightly already reads
every open pull request's title, body and commits each tick, and each
of them would otherwise have to do the same. A file written more than
FRESH ago is not read, so a forge-nightly that has stopped can't keep
pull requests first for ever. The file is on the host forge-nightly
runs on, so it runs on the host of the build check, the Merger and the
router.
"""

import datetime
import os

from agent_forge import locks, state

NIGHTLY_FILE = "nightly.json"
FRESH = datetime.timedelta(minutes=30)


def path(project_name, environ=None, create=False):
    """Where forge-nightly keeps its state."""
    return os.path.join(locks.state_dir(project_name, "nightly", environ,
                                        create=create), NIGHTLY_FILE)


def parse_time(text):
    return datetime.datetime.fromisoformat(
        text.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)


def current(project, environ=None, now=None):
    """What forge-nightly last wrote, or {} without a `nightly` section,
    without the file, or when it is stale (written more than FRESH
    before `now`)."""
    if "nightly" not in project.data:
        return {}
    data = state.load(path(project.name, environ))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        if now - parse_time(data["at"]) > FRESH:
            return {}
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
    return data


def fixes(project, environ=None, now=None):
    """The numbers of the open pull requests that close the open nightly
    issue, as a frozenset: empty without a `nightly` section, without
    the file, when it is stale or when no issue is open."""
    data = current(project, environ, now)
    if not data.get("issue_open"):
        return frozenset()
    numbers = data.get("fixes")
    if not isinstance(numbers, list):
        return frozenset()
    return frozenset(n for n in numbers
                     if isinstance(n, int) and not isinstance(n, bool))
