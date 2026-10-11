"""A project's configuration: `.agent-forge/config.json` in its repository.

Everything project-specific lives there, and nothing in agent-forge
names a project. Tools find the file from the checkout they run in,
never from their own location, so one installed copy serves every
project on a host:

    FORGE_CONFIG        the file itself, if set
    FORGE_PROJECT_ROOT  else the project's checkout, if set
    git rev-parse --show-toplevel, from the working directory, else

`load()` checks the whole file and reports every problem at once, so a
mistake is found at the first run rather than when some rarely taken
path reads the key. Unknown keys are mistakes too: a misspelt key
would otherwise be silently ignored. Keys starting with `_` are
comments. docs/config.md describes each key.
"""

import fnmatch
import json
import os
import re

from agent_forge import proc

CONFIG_DIR = ".agent-forge"
CONFIG_NAME = os.path.join(CONFIG_DIR, "config.json")
KINDS = ("developer", "reviewer")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
AGENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")

# key: (type or tuple of types, required)
TOP = {
    "project": (str, True),
    "repo": (str, True),
    "owner": (str, True),
    "agents": (dict, True),
    "default_branch": (str, False),
    "key_dir": (str, False),
    "naming": (dict, False),
    "machinery": (dict, False),
    "host": (str, False),
    "labels": (dict, False),
    "checks": (dict, False),
    "docs": (dict, False),
    "relay": (dict, False),
    "router": (dict, False),
    "identity": (dict, False),
    "host_settings": (dict, False),
    "merge": (dict, False),
    "receiver": (dict, False),
    "closing_keywords": (dict, False),
}
NAMING = {"app_slug_suffix": str, "session_prefix": str}
APP = {"app_id": (int, type(None)), "installation_id": (int, type(None)),
       "slug": str, "key": str}
AGENT = dict(APP, session_name=str, kind=str, reviews=list)
IDENTITY = {"unattributed_domain": str, "unattributed_name": str}
HOST_SETTINGS = {"files": list, "host_keys": list}
MERGE = {"merger": str, "ci_workflow": str, "gate": list,
         "stall_minutes": int, "stall_alert": dict, "test_ahead": dict,
         "hold_conflicting": bool, "yield_after_hand_back": int,
         "yield_cap": int, "yield_ignores_conflicts": bool,
         "right_of_way": dict, "code_owners": bool}
RIGHT_OF_WAY = {"after_conflicts": int, "minutes": int}
TEST_AHEAD = {"depth": int, "max_age_hours": int}
STALL_ALERT = {"tell": list, "retell_minutes": int,
               "owner_after_minutes": int, "title": str, "labels": list}
# checks is shared with tools not here yet, so only these keys are
# checked.
CHECKS = {"context": str, "poster": str, "steps": list, "caches": dict,
          "slots": int, "memory_max": str, "step_timeout_minutes": int,
          "merger_first": bool, "depends": bool, "depends_minutes": int,
          "tracer": str, "cpu_weight": int, "fast_lane": dict,
          "merge_default": bool, "cache_limit": str, "reuse": dict,
          "fresh": dict}
# checks.cache_limit: a size in systemd's style, at least one byte.
SIZE_RE = re.compile(r"^[1-9][0-9]*[KMGT]?$")
# checks.fast_lane: a slot for heads whose chosen steps are cheap.
FAST_LANE = {"max_seconds": int}
# checks.reuse: a step's green verdict reused for unchanged inputs.
REUSE = {"max_age_days": int}
# checks.fresh: records from every run, and heads that wait for one.
FRESH = {"wait_minutes": int}
STEP = {"name": str, "run": list, "only_if": str, "always": bool,
        "version": list, "reads_base": bool}
# checks.tracer's values (docs/designs/0005).
TRACERS = ("preload", "strace")
# Shared with tools not here yet: only these keys are checked.
LABELS = {"owner_prefix": str, "status_prefix": str,
          "waiting_on_owner": str, "waiting_external": str}
RELAY = {"url": str, "token_file": str, "remind_seconds": int, "room": str}
# forge-router's own: every key is checked.
ROUTER = {"rules": str, "idle_offer": dict, "merge_notice": dict,
          "stuck": dict, "owners": dict}
IDLE_OFFER = {"idle_minutes": int, "report_to": str}
MERGE_NOTICE = {"window_minutes": int}
STUCK = {"retarget": bool, "self_blocked": bool, "draft_hours": int}
OWNERS = {"triage": str}
RECEIVER = {"address": str, "port": int, "path": str, "secret_file": str,
            "max_body": int, "ticks": dict, "route": list}
# The webhook events forge-receiver reads; `receiver.ticks` keys are
# these. bin/forge-receiver says when each one gives a tool work.
RECEIVER_EVENTS = ("issues", "issue_comment", "pull_request",
                   "pull_request_review", "status", "push")
# The receiver reads at most eight digits of Content-Length.
RECEIVER_MAX_BODY = 99999999
CLOSING_KEYWORDS = {"enforced_by": str, "rerun": str}
HOST_FILE = {"from": str, "to": str, "mode": str, "json": bool}
# docs is shared by several tools, some not here yet, so only the keys
# known are checked; another is left to its tool.
DOCS = {"paths": list, "width": int, "roles": str, "designs_dir": str}
MODE_RE = re.compile(r"^0[0-7]{3}$")


class ConfigError(Exception):
    """The configuration can't be found or isn't valid. `problems` lists
    every one found."""

    def __init__(self, problems, path=None):
        self.problems = list(problems)
        self.path = path
        where = "%s: " % path if path else ""
        super().__init__(where + "; ".join(self.problems))


def find(environ=None, cwd=None, run=proc.sh):
    """The configuration file's path, by the order in the module doc."""
    environ = os.environ if environ is None else environ
    if environ.get("FORGE_CONFIG"):
        return os.path.abspath(os.path.expanduser(environ["FORGE_CONFIG"]))
    root = environ.get("FORGE_PROJECT_ROOT")
    if not root:
        got = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
        root = (got.stdout or "").strip()
        if got.returncode != 0 or not root:
            raise ConfigError(["not inside a git checkout, and neither "
                               "FORGE_CONFIG nor FORGE_PROJECT_ROOT is set"])
    return os.path.join(os.path.abspath(os.path.expanduser(root)),
                        CONFIG_NAME)


def load_reviewed(environ=None, cwd=None, run=proc.sh):
    """The configuration the tools that say who an agent is should trust:
    $FORGE_CONFIG if set, else the cron checkout's
    ($FORGE_CRON_CHECKOUT, kept at the default branch), else the one
    `find` finds. Set FORGE_CRON_CHECKOUT in a host's sessions and an
    agent's branch can't change the roster its own App token, push
    credential and signature are judged by. Tools that check the
    branch itself, such as the documentation checks, use `load`."""
    environ = os.environ if environ is None else environ
    checkout = environ.get("FORGE_CRON_CHECKOUT")
    if checkout and not environ.get("FORGE_CONFIG"):
        return load(os.path.join(os.path.abspath(
            os.path.expanduser(checkout)), CONFIG_NAME))
    return load(environ=environ, cwd=cwd, run=run)


def load(path=None, environ=None, cwd=None, run=proc.sh):
    """The checked Project at `path`, or found by `find`."""
    path = path or find(environ, cwd, run)
    try:
        with open(path) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(["no configuration file"], path) from None
    except (OSError, ValueError) as err:
        raise ConfigError(["unreadable: %s" % err], path) from None
    problems = check(data)
    if problems:
        raise ConfigError(problems, path)
    return Project(data, path)


def _is(value, kinds):
    kinds = kinds if isinstance(kinds, tuple) else (kinds,)
    # bool is an int to Python, never to a configuration.
    if isinstance(value, bool) and bool not in kinds:
        return False
    return isinstance(value, kinds)


def _type_name(kinds):
    kinds = kinds if isinstance(kinds, tuple) else (kinds,)
    names = {str: "a string", int: "an integer", bool: "true or false",
             dict: "an object", list: "a list", type(None): "null"}
    return " or ".join(names[k] for k in kinds)


def _fields(where, entry, allowed, problems):
    """Check `entry`'s keys and types against `allowed`."""
    if not isinstance(entry, dict):
        problems.append("%s must be an object" % where)
        return False
    for key, value in entry.items():
        if key.startswith("_"):
            continue
        if key not in allowed:
            problems.append("%s: unknown key %r" % (where, key))
        elif not _is(value, allowed[key]):
            problems.append("%s.%s must be %s"
                            % (where, key, _type_name(allowed[key])))
    return True


def check(data):
    """Every problem with `data`, a parsed configuration; [] if none."""
    problems = []
    if not isinstance(data, dict):
        return ["the configuration must be a JSON object"]
    for key, (kinds, required) in TOP.items():
        if key not in data:
            if required:
                problems.append("missing %r" % key)
        elif not _is(data[key], kinds):
            problems.append("%r must be %s" % (key, _type_name(kinds)))
    for key in data:
        if not key.startswith("_") and key not in TOP:
            problems.append("unknown key %r" % key)
    if problems:
        return problems
    if not NAME_RE.match(data["project"]):
        problems.append("project %r must be lower case letters, digits and "
                        "hyphens" % data["project"])
    if not REPO_RE.match(data["repo"]):
        problems.append("repo %r must be owner/name" % data["repo"])
    _fields("naming", data.get("naming", {}), NAMING, problems)
    _fields("identity", data.get("identity", {}), IDENTITY, problems)
    router = data.get("router", {})
    if _fields("router", router, ROUTER, problems):
        if isinstance(router.get("idle_offer"), dict):
            _check_idle_offer(router["idle_offer"], data["agents"],
                              problems)
        if isinstance(router.get("merge_notice"), dict):
            _check_merge_notice(router["merge_notice"], problems)
        if isinstance(router.get("stuck"), dict):
            _check_stuck(router["stuck"], problems)
        if isinstance(router.get("owners"), dict):
            _check_owners(router["owners"], data["agents"], problems)
    merge = data.get("merge", {})
    if _fields("merge", merge, MERGE, problems):
        for i, argv in enumerate(merge.get("gate", [])):
            if not (isinstance(argv, list) and argv
                    and all(isinstance(a, str) for a in argv)):
                problems.append("merge.gate[%d] must be a non-empty list of "
                                "strings" % i)
        name = merge.get("merger")
        if isinstance(name, str) and name not in data.get("machinery", {}):
            problems.append("merge.merger %r is not in machinery" % name)
        if isinstance(merge.get("stall_alert"), dict):
            _check_stall_alert(merge["stall_alert"], data["agents"], problems)
        if isinstance(merge.get("test_ahead"), dict):
            _check_test_ahead(merge["test_ahead"], data.get("checks"),
                              problems)
        for key in ("yield_after_hand_back", "yield_cap"):
            if _is(merge.get(key), int) and merge[key] < 1:
                problems.append("merge.%s must be at least 1" % key)
        if "yield_cap" in merge and "yield_after_hand_back" not in merge:
            problems.append("merge.yield_cap needs merge.yield_after_hand_back")
        if isinstance(merge.get("right_of_way"), dict):
            row = merge["right_of_way"]
            _fields("merge.right_of_way", row, RIGHT_OF_WAY, problems)
            for key in RIGHT_OF_WAY:
                if _is(row.get(key), int) and row[key] < 1:
                    problems.append("merge.right_of_way.%s must be at least 1"
                                    % key)
    for name, allowed in (("labels", LABELS), ("relay", RELAY)):
        section = data.get(name, {})
        for key, kinds in allowed.items():
            if key in section and not _is(section[key], kinds):
                problems.append("%s.%s must be %s"
                                % (name, key, _type_name(kinds)))
    if _is(data.get("relay", {}).get("remind_seconds"), int) and \
            data["relay"]["remind_seconds"] < 1:
        problems.append("relay.remind_seconds must be at least 1")
    checks = data.get("checks", {})
    for key, kinds in CHECKS.items():
        if key in checks and not _is(checks[key], kinds):
            problems.append("checks.%s must be %s" % (key, _type_name(kinds)))
    for key in ("slots", "step_timeout_minutes", "depends_minutes"):
        if _is(checks.get(key), int) and checks[key] < 1:
            problems.append("checks.%s must be at least 1" % key)
    if _is(checks.get("cpu_weight"), int) \
            and not 0 <= checks["cpu_weight"] <= 10000:
        problems.append("checks.cpu_weight must be 0 (none) or 1 to 10000")
    if isinstance(checks.get("fast_lane"), dict):
        _check_fast_lane(checks, problems)
    if isinstance(checks.get("reuse"), dict):
        _check_reuse(checks, problems)
    if isinstance(checks.get("fresh"), dict):
        _check_fresh(checks, problems)
    if isinstance(checks.get("cache_limit"), str) \
            and not SIZE_RE.match(checks["cache_limit"]):
        problems.append("checks.cache_limit must be a size such as 40G "
                        "(K, M, G or T)")
    if isinstance(checks.get("tracer"), str) \
            and checks["tracer"] not in TRACERS:
        problems.append("checks.tracer must be one of %s"
                        % ", ".join(TRACERS))
    if isinstance(checks.get("steps"), list):
        for i, step in enumerate(checks["steps"]):
            where = "checks.steps[%d]" % i
            if not _fields(where, step, STEP, problems):
                continue
            if "name" not in step:
                problems.append("%s: missing 'name'" % where)
            run = step.get("run")
            if not (isinstance(run, list) and run
                    and all(isinstance(a, str) for a in run)):
                problems.append("%s.run must be a non-empty list of strings"
                                % where)
            version = step.get("version")
            if "version" in step and not (
                    isinstance(version, list) and version
                    and all(isinstance(a, str) for a in version)):
                problems.append("%s.version must be a non-empty list of "
                                "strings" % where)
    if isinstance(checks.get("caches"), dict) and not all(
            isinstance(v, str) and v and "/" not in v
            for v in checks["caches"].values()):
        problems.append("checks.caches must map variables to directory names")
    poster = checks.get("poster")
    if isinstance(poster, str) and poster not in data.get("machinery", {}) \
            and poster not in data.get("agents", {}):
        problems.append("checks.poster %r is no App in the configuration"
                        % poster)
    _check_receiver(data.get("receiver", {}), problems)
    closing = data.get("closing_keywords", {})
    if _fields("closing_keywords", closing, CLOSING_KEYWORDS, problems):
        for key in CLOSING_KEYWORDS:
            if isinstance(closing.get(key), str) and not closing[key].strip():
                problems.append("closing_keywords.%s must not be empty" % key)
    settings = data.get("host_settings", {})
    if _fields("host_settings", settings, HOST_SETTINGS, problems):
        for i, entry in enumerate(settings.get("files", [])):
            where = "host_settings.files[%d]" % i
            if not _fields(where, entry, HOST_FILE, problems):
                continue
            for key in ("from", "to"):
                if key not in entry:
                    problems.append("%s: missing %r" % (where, key))
                elif entry[key].startswith("/") or ".." in entry[key].split("/"):
                    problems.append("%s.%s must be a relative path inside "
                                    "its tree" % (where, key))
            if "mode" in entry and not MODE_RE.match(entry["mode"]):
                problems.append("%s.mode must be octal, like \"0644\""
                                % where)
        if not all(isinstance(k, str) for k in settings.get("host_keys", [])):
            problems.append("host_settings.host_keys must be a list of "
                            "strings")
    docs = data.get("docs", {})
    for key, kinds in DOCS.items():
        if key in docs and not _is(docs[key], kinds):
            problems.append("docs.%s must be %s" % (key, _type_name(kinds)))
    if isinstance(docs.get("paths"), list) and \
            not all(isinstance(p, str) and p for p in docs["paths"]):
        problems.append("docs.paths must be a list of path globs")
    if _is(docs.get("width"), int) and docs["width"] < 1:
        problems.append("docs.width must be at least 1")
    suffix = data.get("naming", {}).get("app_slug_suffix")
    if not data["agents"]:
        problems.append("agents must name at least one agent")
    for name, entry in data["agents"].items():
        where = "agents.%s" % name
        if not AGENT_RE.match(name):
            problems.append("%s: an agent's name is letters and digits, "
                            "starting with a letter" % where)
        if not _fields(where, entry, AGENT, problems):
            continue
        kind = entry.get("kind", "developer")
        if kind not in KINDS:
            problems.append("%s.kind must be one of %s"
                            % (where, ", ".join(KINDS)))
        if "reviews" in entry:
            if kind != "reviewer":
                problems.append("%s.reviews: only a reviewer reviews paths"
                                % where)
            elif not all(isinstance(g, str) and g for g in entry["reviews"]):
                problems.append("%s.reviews must be a list of path globs"
                                % where)
        if "slug" not in entry and not suffix:
            problems.append("%s: no slug, and no naming.app_slug_suffix to "
                            "make one" % where)
    for name, entry in data.get("machinery", {}).items():
        where = "machinery.%s" % name
        if _fields(where, entry, APP, problems) and "slug" not in entry \
                and not suffix:
            problems.append("%s: no slug, and no naming.app_slug_suffix to "
                            "make one" % where)
    clash = set(data["agents"]) & set(data.get("machinery", {}))
    for name in sorted(clash):
        problems.append("%s is both an agent and machinery" % name)
    return problems


def _argv(value):
    """Whether `value` is a non-empty list of strings whose first names a
    tool as forge-cron-run takes one: a bare name, no path."""
    return (isinstance(value, list) and value
            and all(isinstance(a, str) and a for a in value)
            and "/" not in value[0] and not value[0].startswith("."))


def _check_stall_alert(alert, agents, problems):
    """merge.stall_alert: whom the Merger tells of a stalled queue, and
    how its issue looks. Only agents can be told: the relay reaches
    sessions, and machinery has none."""
    where = "merge.stall_alert"
    _fields(where, alert, STALL_ALERT, problems)
    tell = alert.get("tell")
    if isinstance(tell, list):
        for name in tell:
            if not isinstance(name, str) or name not in agents:
                problems.append("%s.tell: %r is not an agent" % (where, name))
    for key in ("retell_minutes", "owner_after_minutes"):
        if _is(alert.get(key), int) and alert[key] < 1:
            problems.append("%s.%s must be at least 1" % (where, key))
    if isinstance(alert.get("title"), str) and not alert["title"].strip():
        problems.append("%s.title must not be empty" % where)
    labels = alert.get("labels")
    if isinstance(labels, list) and not all(
            isinstance(l, str) and l.strip() for l in labels):
        problems.append("%s.labels must be a list of label names" % where)


def _check_test_ahead(ahead, checks, problems):
    """merge.test_ahead: how far ahead the Merger predicts, and how long
    the build check reuses a green verdict for another commit with its
    tree. Testing ahead puts the Merger's pull request first, so
    `checks.merger_first: false` contradicts it."""
    where = "merge.test_ahead"
    _fields(where, ahead, TEST_AHEAD, problems)
    if _is(ahead.get("depth"), int) and ahead["depth"] < 0:
        problems.append("%s.depth must be at least 0" % where)
    if _is(ahead.get("max_age_hours"), int) and ahead["max_age_hours"] < 1:
        problems.append("%s.max_age_hours must be at least 1" % where)
    if isinstance(checks, dict) and checks.get("merger_first") is False:
        problems.append("%s puts the Merger's pull request first, so "
                        "checks.merger_first must not be false" % where)


def _check_fast_lane(checks, problems):
    """checks.fast_lane: a slot of its own for heads whose chosen steps
    have taken at most max_seconds here. Only `depends` chooses steps,
    so without it nothing could be cheap."""
    where = "checks.fast_lane"
    lane = checks["fast_lane"]
    _fields(where, lane, FAST_LANE, problems)
    if _is(lane.get("max_seconds"), int) and lane["max_seconds"] < 1:
        problems.append("%s.max_seconds must be at least 1" % where)
    if checks.get("depends") is not True:
        problems.append("%s needs checks.depends: without it every head "
                        "runs every step, and none is cheap" % where)


def _check_fresh(checks, problems):
    """checks.fresh (designs/0010): dependency records from every green
    run, and how long a head waits for a fresh one. Records are what
    `depends` writes, so it needs it."""
    where = "checks.fresh"
    fresh = checks["fresh"]
    _fields(where, fresh, FRESH, problems)
    if _is(fresh.get("wait_minutes"), int) and fresh["wait_minutes"] < 1:
        problems.append("%s.wait_minutes must be at least 1" % where)
    if checks.get("depends") is not True:
        problems.append("%s needs checks.depends: it keeps the dependency "
                        "records depends writes fresh" % where)


def _check_reuse(checks, problems):
    """checks.reuse (designs/0009): how old a step's green verdict may
    be to stand for unchanged inputs. The inputs are read from the
    dependency record, so it needs `depends`."""
    where = "checks.reuse"
    reuse = checks["reuse"]
    _fields(where, reuse, REUSE, problems)
    if _is(reuse.get("max_age_days"), int) and reuse["max_age_days"] < 1:
        problems.append("%s.max_age_days must be at least 1" % where)
    if checks.get("depends") is not True:
        problems.append("%s needs checks.depends: a step's inputs are "
                        "what its dependency record says it read" % where)


def _check_idle_offer(offer, agents, problems):
    """router.idle_offer: how long an agent is idle before it is offered
    its next item, and who is told of a developer with none."""
    where = "router.idle_offer"
    _fields(where, offer, IDLE_OFFER, problems)
    if _is(offer.get("idle_minutes"), int) and offer["idle_minutes"] < 1:
        problems.append("%s.idle_minutes must be at least 1" % where)
    if isinstance(offer.get("report_to"), str) and \
            offer["report_to"] not in agents:
        problems.append("%s.report_to: %r is not an agent"
                        % (where, offer["report_to"]))


def _check_merge_notice(notice, problems):
    """router.merge_notice: how far back a run looks for merges to tell
    their authors of."""
    where = "router.merge_notice"
    _fields(where, notice, MERGE_NOTICE, problems)
    if _is(notice.get("window_minutes"), int) and \
            notice["window_minutes"] < 1:
        problems.append("%s.window_minutes must be at least 1" % where)


def _check_stuck(stuck, problems):
    """router.stuck: which of the default rules' stuck pull request
    rules are on, and how long a draft sits before it is asked about."""
    where = "router.stuck"
    _fields(where, stuck, STUCK, problems)
    if _is(stuck.get("draft_hours"), int) and stuck["draft_hours"] < 1:
        problems.append("%s.draft_hours must be at least 1" % where)


def _check_owners(owners, agents, problems):
    """router.owners (designs/0008): who is asked to label an issue
    nobody owns."""
    where = "router.owners"
    _fields(where, owners, OWNERS, problems)
    if isinstance(owners.get("triage"), str) and \
            owners["triage"] not in agents:
        problems.append("%s.triage: %r is not an agent"
                        % (where, owners["triage"]))


def _check_receiver(receiver, problems):
    if not _fields("receiver", receiver, RECEIVER, problems):
        return
    port = receiver.get("port")
    if _is(port, int) and not 1 <= port <= 65535:
        problems.append("receiver.port must be from 1 to 65535")
    path = receiver.get("path")
    if isinstance(path, str) and not path.startswith("/"):
        problems.append("receiver.path must start with /")
    cap = receiver.get("max_body")
    if _is(cap, int) and not 1 <= cap <= RECEIVER_MAX_BODY:
        problems.append("receiver.max_body must be from 1 to %d"
                        % RECEIVER_MAX_BODY)
    for event, tools in receiver.get("ticks", {}).items():
        where = "receiver.ticks.%s" % event
        if event.startswith("_"):
            continue
        if event not in RECEIVER_EVENTS:
            problems.append("%s: not an event the receiver reads (%s)"
                            % (where, ", ".join(RECEIVER_EVENTS)))
            continue
        if not isinstance(tools, list):
            problems.append("%s must be a list" % where)
            continue
        for i, tool in enumerate(tools):
            if not (_argv([tool]) if isinstance(tool, str) else _argv(tool)):
                problems.append("%s[%d] must be a tool's name or a non-empty "
                                "list of strings starting with one"
                                % (where, i))
    route = receiver.get("route")
    if isinstance(route, list) and not (
            _argv(route) and any("{items}" in a for a in route)):
        problems.append("receiver.route must be a non-empty list of strings "
                        "starting with a tool's name, with {items} in one")


class Project:
    """A checked configuration, with what tools ask of it."""

    def __init__(self, data, path=None):
        self.data = data
        self.path = path
        self.name = data["project"]
        self.repo = data["repo"]
        self.owner = data["owner"]
        self.default_branch = data.get("default_branch", "main")
        self.host = data.get("host", "")
        self.agents = data["agents"]
        self.machinery = data.get("machinery", {})
        naming = data.get("naming", {})
        self._suffix = naming.get("app_slug_suffix", "")
        self._prefix = naming.get("session_prefix", "")

    @property
    def root(self):
        """The project's checkout, where the file was found there."""
        if self.path and self.path.endswith(os.sep + CONFIG_NAME):
            return self.path[:-len(os.sep + CONFIG_NAME)]
        return None

    def section(self, key):
        """A section (labels, checks, docs, relay, router), or {}."""
        return self.data.get(key, {})

    def app(self, name):
        """The agent's or machinery App's entry; KeyError if neither."""
        if name in self.agents:
            return self.agents[name]
        return self.machinery[name]

    def slug(self, name):
        """The App's slug, lower case, as GitHub stores it."""
        entry = self.app(name)
        return (entry.get("slug") or name + self._suffix).lower()

    def bot_login(self, name):
        """The App's login as REST reports it."""
        return self.slug(name) + "[bot]"

    def by_login(self, login):
        """The agent or machinery name whose App has `login`, with or
        without GitHub's "[bot]" suffix, or None."""
        login = login[:-len("[bot]")] if login.endswith("[bot]") else login
        for name in list(self.agents) + list(self.machinery):
            if self.slug(name) == login.lower():
                return name
        return None

    def kind(self, name):
        return self.agents[name].get("kind", "developer")

    def reviewers(self):
        """Reviewer agents, in the configuration's order."""
        return [n for n in self.agents if self.kind(n) == "reviewer"]

    def developers(self):
        return [n for n in self.agents if self.kind(n) == "developer"]

    def session_name(self, name):
        """The name the agent's session is woken by."""
        return self.agents[name].get("session_name") or self._prefix + name

    def exists(self, name):
        """Whether the App and its installation exist: null ids mean an
        App not yet made, which no tool may mint for."""
        entry = self.app(name)
        return entry.get("app_id") is not None \
            and entry.get("installation_id") is not None

    def key_path(self, name):
        """Where the App's private key is, or None with no key_dir."""
        key_dir = self.data.get("key_dir")
        if not key_dir:
            return None
        return os.path.join(os.path.expanduser(key_dir),
                            self.app(name).get("key") or name + ".pem")

    def reviewers_for(self, path):
        """The reviewers whose globs match `path`, in order."""
        return [n for n in self.reviewers()
                if any(fnmatch.fnmatchcase(path, g)
                       for g in self.agents[n].get("reviews", []))]

    def get(self, dotted):
        """A value by dotted path ("agents.Reviewer.kind"); KeyError if
        absent."""
        value = self.data
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError(dotted)
            value = value[part]
        return value
