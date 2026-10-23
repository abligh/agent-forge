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
KINDS = ("developer", "reviewer", "collaborator")
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
    "memory": (dict, False),
    "quiet": (dict, False),
    "queue": (dict, False),
    "stats_watch": (dict, False),
    "nightly": (dict, False),
}
NAMING = {"app_slug_suffix": str, "session_prefix": str}
APP = {"app_id": (int, type(None)), "installation_id": (int, type(None)),
       "slug": str, "key": str}
AGENT = dict(APP, session_name=str, twins=list, kind=str, reviews=list,
             away=str)
IDENTITY = {"unattributed_domain": str, "unattributed_name": str}
HOST_SETTINGS = {"files": list, "host_keys": list, "crontabs": dict}
# forge-memory-sync, -check and -trim (agent_forge/memory.py).
MEMORY = {"dir": str, "remote": str, "git_dir": str, "seed_dir": str,
          "ssh_key": str, "branch": str, "labels": list, "limit": int,
          "long_line": int}
# forge-quiet and what honours a hold (agent_forge/quiet.py).
QUIET = {"dir": str, "max_minutes": int, "settle": list,
         "settle_minutes": int}
# forge-queue (designs/0023): named queues of slow jobs.
QUEUE = {"queues": dict, "state_dir": str, "retain_days": int}
QUEUE_ENTRY = {"slots": int, "nice": int, "timeout_minutes": int,
               "keep_worktree": bool}
# A queue's name starts its tickets and names its directory.
QUEUE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# forge-stats-watch (designs/0024): a watch on forge-stats' figures.
STATS_WATCH = {"owner": str, "window_hours": int, "baseline_days": int,
               "figures": dict, "label": str, "remind_hours": int,
               "min_samples": int, "as": str}
# Each watched figure, and the rules it takes: `limit` and `ratio`.
STATS_WATCH_FIGURES = {
    "check_run": ("limit", "ratio"), "check_wait": ("limit", "ratio"),
    "pr_turnaround": ("limit", "ratio"), "merge_time": ("limit", "ratio"),
    "merge_rate": ("ratio",), "slots_busy": ("limit",), "main_red": (),
    "nightly_red_hours": ("limit",)}
# forge-nightly (designs/0028): a red nightly is a red main.
NIGHTLY = {"workflow": str, "job": str, "owners": dict, "labels": list,
           "retry_hours": int, "escalate_hours": list, "as": str,
           "tell_owner": str}
# A workflow is named by its file, which is read for its inputs.
WORKFLOW_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
MERGE = {"merger": str, "ci_workflow": str, "gate": list,
         "stall_minutes": int, "stall_alert": dict, "test_ahead": dict,
         "hold_conflicting": bool, "yield_after_hand_back": int,
         "yield_cap": int, "yield_ignores_conflicts": bool,
         "right_of_way": dict, "code_owners": bool,
         "early_conflicts": bool, "stacks": dict, "resolve": list,
         "hold_labels": list, "blocked_by": str, "said_since": dict,
         "update_once": bool, "ci_never_started": int,
         "queued_stall_minutes": int, "read_as": str}
RESOLVE_RULE = {"paths": list, "command": list, "check": list}
SAID_SINCE = {"author": bool, "approvals": bool, "quiet": list}
BLOCKED_BY = ("body", "everywhere")
RIGHT_OF_WAY = {"after_conflicts": int, "minutes": int}
STACKS = {"max_run": int}
TEST_AHEAD = {"depth": int, "max_age_hours": int, "spare_slots_only": bool}
STALL_ALERT = {"tell": list, "retell_minutes": int,
               "owner_after_minutes": int, "title": str, "labels": list}
# checks is shared with tools not here yet, so only these keys are
# checked.
CHECKS = {"context": str, "poster": str, "steps": list, "caches": dict,
          "slots": int, "memory_max": (str, type(None)), "step_timeout_minutes": int,
          "merger_first": bool, "depends": bool, "depends_minutes": int,
          "tracer": str, "cpu_weight": int, "fast_lane": dict,
          "merge_default": bool, "cache_limit": str, "reuse": dict,
          "fresh": dict, "first_authors": list, "max_wait_minutes": int,
          "main": str, "main_red_issue": dict, "drop_env": list,
          "keep_ignored": list, "red_comment": bool, "instances": dict,
          "host_fault_text": list, "step_alert": dict}
# checks.step_alert (designs/0019): an issue when a step gets slower.
STEP_ALERT = {"growth": (int, float), "min_seconds": int, "window_days": int,
              "min_runs": int, "labels": list, "owner": str}
# checks.instances (designs/0021): more instances of the check, each on
# its own host with its own context and steps. A block's keys come from
# the block alone (OWN), from the block or else `checks` (INHERITED),
# from the block alone and never inherited, because they describe the
# first host's tracer (BLOCK_ONLY), or from `checks` alone, because they
# belong to the context the Merger requires (CHECKS_ONLY).
INSTANCE_OWN = ("host", "context", "steps", "poster")
INSTANCE_INHERITED = ("slots", "memory_max", "cpu_weight",
                      "step_timeout_minutes", "caches", "cache_limit",
                      "keep_ignored", "drop_env", "merge_default",
                      "red_comment", "max_wait_minutes", "host_fault_text",
                      "step_alert")
INSTANCE_BLOCK_ONLY = ("depends", "tracer", "depends_minutes", "fast_lane",
                       "reuse", "fresh", "main")
INSTANCE_CHECKS_ONLY = ("main_red_issue", "first_authors", "merger_first")
# An instance's name names its state directory and its log lines.
INSTANCE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# checks.drop_env: a variable's name, or a prefix ending in `*`.
DROP_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\*?$")
# checks.main: which default-branch heads are run (designs/0011).
MAIN_MODES = ("needed", "every")
MAIN_RED_ISSUE = {"label": str, "labels": list, "title": str}
# checks.cache_limit: a size in systemd's style, at least one byte.
SIZE_RE = re.compile(r"^[1-9][0-9]*[KMGT]?$")
# checks.fast_lane: a slot for heads whose chosen steps are cheap.
FAST_LANE = {"max_seconds": int}
# checks.reuse: a step's green verdict reused for unchanged inputs.
REUSE = {"max_age_days": int, "trace_matched_main": bool,
         "chain_writes_only": bool, "dirs_by_names": bool}
# checks.fresh: records from every run, and heads that wait for one.
FRESH = {"wait_minutes": int}
STEP = {"name": str, "run": list, "only_if": str, "always": bool,
        "version": list, "reads_base": bool, "outputs": list}
# checks.tracer's values (docs/designs/0005).
TRACERS = ("preload", "strace")
# Shared with tools not here yet: only these keys are checked.
LABELS = {"owner_prefix": str, "status_prefix": str,
          "waiting_on_owner": str, "waiting_external": str, "urgent": str,
          "rerun": str}
RELAY = {"url": str, "token_file": str, "remind_seconds": int, "room": str}
# forge-router's own: every key is checked.
ROUTER = {"rules": str, "idle_offer": dict, "merge_notice": dict,
          "stuck": dict, "owners": dict, "unreviewed": dict}
IDLE_OFFER = {"idle_minutes": int, "shell_minutes": int, "report_to": str}
MERGE_NOTICE = {"window_minutes": int}
STUCK = {"retarget": bool, "self_blocked": bool, "draft_hours": int}
OWNERS = {"triage": str}
UNREVIEWED = {"ignore": list, "depth2": list}
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


def _check_memory(memory, present, problems):
    """The `memory` section: where the agents' memories are shared."""
    if not _fields("memory", memory, MEMORY, problems) or not present:
        return
    for key in ("dir", "remote"):
        if key not in memory:
            problems.append("memory: missing %r" % key)
    for key in ("dir", "remote", "git_dir", "seed_dir", "ssh_key", "branch"):
        if isinstance(memory.get(key), str) and not memory[key].strip():
            problems.append("memory.%s must not be empty" % key)
    if isinstance(memory.get("labels"), list) and not all(
            isinstance(x, str) and x for x in memory["labels"]):
        problems.append("memory.labels must be a list of label names")
    for key in ("limit", "long_line"):
        if _is(memory.get(key), int) and memory[key] < 1:
            problems.append("memory.%s must be at least 1" % key)


def _check_quiet(quiet, present, problems):
    """The `quiet` section: where a shared host's holds are."""
    if not _fields("quiet", quiet, QUIET, problems) or not present:
        return
    if not isinstance(quiet.get("dir"), str) or not quiet["dir"].strip():
        problems.append("quiet: missing 'dir'")
    if _is(quiet.get("max_minutes"), int) and quiet["max_minutes"] < 1:
        problems.append("quiet.max_minutes must be at least 1")
    settle = quiet.get("settle")
    if settle is not None and not (
            settle and all(isinstance(a, str) and a for a in settle)):
        problems.append("quiet.settle must be a non-empty list of strings")
    if _is(quiet.get("settle_minutes"), int) and quiet["settle_minutes"] < 1:
        problems.append("quiet.settle_minutes must be at least 1")


def _check_queue(queue, problems):
    """The `queue` section: forge-queue's queues."""
    if not _fields("queue", queue, QUEUE, problems):
        return
    if isinstance(queue.get("state_dir"), str) and \
            not queue["state_dir"].strip():
        problems.append("queue.state_dir must not be empty")
    if _is(queue.get("retain_days"), int) and queue["retain_days"] < 1:
        problems.append("queue.retain_days must be at least 1")
    for name, entry in (queue.get("queues") or {}).items():
        where = "queue.queues.%s" % name
        if not QUEUE_NAME_RE.match(name):
            problems.append("%s: a queue's name is lower case letters, "
                            "digits and hyphens" % where)
        if not _fields(where, entry, QUEUE_ENTRY, problems):
            continue
        for key in ("slots", "timeout_minutes"):
            if _is(entry.get(key), int) and entry[key] < 1:
                problems.append("%s.%s must be at least 1" % (where, key))
        if _is(entry.get("nice"), int) and not 0 <= entry["nice"] <= 19:
            problems.append("%s.nice must be from 0 to 19" % where)


def _check_stats_watch(watch, data, problems):
    """The `stats_watch` section: forge-stats-watch's figures, and the
    agent its issues are for."""
    where = "stats_watch"
    if not _fields(where, watch, STATS_WATCH, problems):
        return
    owner = watch.get("owner")
    if owner is None:
        problems.append("%s: missing 'owner'" % where)
    elif isinstance(owner, str) and owner not in data["agents"]:
        problems.append("%s.owner %r is not an agent" % (where, owner))
    name = watch.get("as")
    if name is None:
        name = data.get("merge", {}).get("merger", "Merger")
    if isinstance(name, str) and name not in data["agents"] \
            and name not in data.get("machinery", {}):
        problems.append("%s.as %r is not an agent or machinery (it "
                        "defaults to the Merger)" % (where, name))
    for key in ("window_hours", "baseline_days", "remind_hours",
                "min_samples"):
        if _is(watch.get(key), int) and watch[key] < 1:
            problems.append("%s.%s must be at least 1" % (where, key))
    if isinstance(watch.get("label"), str) and not watch["label"].strip():
        problems.append("%s.label must not be empty" % where)
    for figure, rules in (watch.get("figures") or {}).items():
        at = "%s.figures.%s" % (where, figure)
        if figure not in STATS_WATCH_FIGURES:
            problems.append("%s: unknown figure (one of %s)"
                            % (at, ", ".join(STATS_WATCH_FIGURES)))
            continue
        if not isinstance(rules, dict):
            problems.append("%s must be an object" % at)
            continue
        for key, value in rules.items():
            if key.startswith("_"):
                continue
            if key not in STATS_WATCH_FIGURES[figure]:
                problems.append("%s: unknown key %r" % (at, key))
            elif not _is(value, (int, float)) or value <= 0:
                problems.append("%s.%s must be a number above 0"
                                % (at, key))
            elif key == "ratio" and value <= 1:
                problems.append("%s.ratio must be more than 1" % at)


def _check_nightly(nightly, data, problems):
    """The `nightly` section: forge-nightly's workflow and job, who owns
    which of its steps, and when it retries and escalates."""
    where = "nightly"
    if not _fields(where, nightly, NIGHTLY, problems):
        return
    for key in ("workflow", "job"):
        if key not in nightly:
            problems.append("%s: missing %r" % (where, key))
        elif isinstance(nightly[key], str) and not nightly[key].strip():
            problems.append("%s.%s must not be empty" % (where, key))
    if isinstance(nightly.get("workflow"), str) and nightly["workflow"] \
            and not WORKFLOW_FILE_RE.match(nightly["workflow"]):
        problems.append("%s.workflow must be the workflow's file name, "
                        "such as ci.yaml" % where)
    if not isinstance(data.get("router", {}).get("owners", {}).get(
            "triage"), str):
        problems.append("%s needs router.owners.triage: a failed step "
                        "that `owners` doesn't name is hers" % where)
    for pattern, agent in (nightly.get("owners") or {}).items():
        if pattern.startswith("_"):
            continue
        if not isinstance(agent, str) or agent not in data["agents"]:
            problems.append("%s.owners.%s: %r is not an agent"
                            % (where, pattern, agent))
    labels = nightly.get("labels")
    if isinstance(labels, list) and not all(
            isinstance(l, str) and l.strip() for l in labels):
        problems.append("%s.labels must be a list of label names" % where)
    if _is(nightly.get("retry_hours"), int) and nightly["retry_hours"] < 1:
        problems.append("%s.retry_hours must be at least 1" % where)
    hours = nightly.get("escalate_hours")
    if isinstance(hours, list) and not (
            1 <= len(hours) <= 2
            and all(_is(h, int) and h >= 1 for h in hours)
            and hours == sorted(set(hours))):
        problems.append("%s.escalate_hours must be one or two whole "
                        "numbers of hours, at least 1, rising (the "
                        "developers, then the owner)" % where)
    if isinstance(nightly.get("tell_owner"), str) \
            and not nightly["tell_owner"].strip():
        problems.append("%s.tell_owner must not be empty" % where)
    name = nightly.get("as")
    if name is None:
        name = data.get("merge", {}).get("merger", "Merger")
    if isinstance(name, str) and name not in data["agents"] \
            and name not in data.get("machinery", {}):
        problems.append("%s.as %r is not an agent or machinery (it "
                        "defaults to the Merger)" % (where, name))


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
        if isinstance(router.get("unreviewed"), dict):
            _check_unreviewed(router["unreviewed"], router, problems)
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
        if isinstance(merge.get("stacks"), dict):
            stacks = merge["stacks"]
            _fields("merge.stacks", stacks, STACKS, problems)
            if _is(stacks.get("max_run"), int) and stacks["max_run"] < 0:
                problems.append("merge.stacks.max_run must be at least 0")
        _check_merge_0026(merge, data, problems)
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
    _check_checks(checks, data, problems)
    if isinstance(checks.get("instances"), dict):
        _check_instances(checks["instances"], data, problems)
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
        for host, rel in settings.get("crontabs", {}).items():
            where = "host_settings.crontabs[%r]" % host
            if not host or "." in host:
                problems.append("%s: a host is its short name, with no dot"
                                % where)
            if not isinstance(rel, str) or not rel or rel.startswith("/") \
                    or ".." in rel.split("/"):
                problems.append("%s must be a relative path inside its tree"
                                % where)
        if not all(isinstance(k, str) for k in settings.get("host_keys", [])):
            problems.append("host_settings.host_keys must be a list of "
                            "strings")
    _check_memory(data.get("memory", {}), "memory" in data, problems)
    _check_quiet(data.get("quiet", {}), "quiet" in data, problems)
    _check_queue(data.get("queue", {}), problems)
    if "stats_watch" in data:
        _check_stats_watch(data["stats_watch"], data, problems)
    if "nightly" in data:
        _check_nightly(data["nightly"], data, problems)
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
        if "twins" in entry and not all(isinstance(s, str) and s
                                        for s in entry["twins"]):
            problems.append("%s.twins must be a list of session names"
                            % where)
        if "slug" not in entry and not suffix:
            problems.append("%s: no slug, and no naming.app_slug_suffix to "
                            "make one" % where)
    prefix = data.get("naming", {}).get("session_prefix", "")
    holders = {}
    for name, entry in data["agents"].items():
        if not isinstance(entry, dict):
            continue
        twins = entry.get("twins", [])
        for session in [entry.get("session_name") or prefix + name] \
                + (twins if isinstance(twins, list) else []):
            if isinstance(session, str):
                holders.setdefault(session, []).append(name)
    for session in sorted(holders):
        if len(holders[session]) > 1:
            problems.append("session %s is named more than once (%s): "
                            "the relay wakes a name only when one session "
                            "holds it" % (session,
                                          ", ".join(holders[session])))
    for name, entry in data["agents"].items():
        away = entry.get("away") if isinstance(entry, dict) else None
        if not isinstance(away, str):
            continue
        where = "agents.%s.away" % name
        other = data["agents"].get(away)
        if away == name or not isinstance(other, dict):
            problems.append("%s: %r is not another agent" % (where, away))
        elif other.get("away"):
            problems.append("%s: %s is away too; a stand-in must be here"
                            % (where, away))
        elif entry.get("kind", "developer") == "reviewer" \
                and other.get("kind", "developer") != "reviewer":
            problems.append("%s: %s reviews, so her stand-in must be a "
                            "reviewer" % (where, name))
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


def _strings(value):
    """Whether `value` is a non-empty list of non-empty strings."""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(x, str) and x.strip() for x in value))


def _check_merge_0026(merge, data, problems):
    """The `merge` settings of designs/0026."""
    names = set(data.get("agents") or {}) | set(data.get("machinery") or {})
    rules = merge.get("resolve")
    if isinstance(rules, list):
        for i, rule in enumerate(rules):
            where = "merge.resolve[%d]" % i
            if not _fields(where, rule, RESOLVE_RULE, problems):
                continue
            for key in RESOLVE_RULE:
                if key not in rule:
                    problems.append("%s: missing %r" % (where, key))
                elif not _strings(rule[key]):
                    problems.append("%s.%s must be a non-empty list of "
                                    "strings" % (where, key))
    labels = merge.get("hold_labels")
    if isinstance(labels, list) and not all(
            isinstance(l, str) and l.strip() for l in labels):
        problems.append("merge.hold_labels must be a list of label names")
    if isinstance(merge.get("blocked_by"), str) and \
            merge["blocked_by"] not in BLOCKED_BY:
        problems.append("merge.blocked_by must be one of %s"
                        % ", ".join(BLOCKED_BY))
    said = merge.get("said_since")
    if isinstance(said, dict):
        _fields("merge.said_since", said, SAID_SINCE, problems)
        if isinstance(said.get("quiet"), list):
            for name in said["quiet"]:
                if not isinstance(name, str) or name not in names:
                    problems.append("merge.said_since.quiet: %r is not an "
                                    "agent or machinery" % (name,))
    for key in ("ci_never_started", "queued_stall_minutes"):
        if _is(merge.get(key), int) and merge[key] < 1:
            problems.append("merge.%s must be at least 1" % key)
    if isinstance(merge.get("read_as"), str) and merge["read_as"] not in names:
        problems.append("merge.read_as %r is not an agent or machinery"
                        % merge["read_as"])


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


def _check_step_alert(alert, data, problems, where):
    """checks.step_alert (designs/0019)."""
    if not _fields(where, alert, STEP_ALERT, problems):
        return
    growth = alert.get("growth")
    if _is(growth, (int, float)) and not isinstance(growth, bool) \
            and growth <= 1:
        problems.append("%s.growth must be above 1" % where)
    for key, low in (("min_seconds", 0), ("min_runs", 1)):
        if isinstance(alert.get(key), int) and alert[key] < low:
            problems.append("%s.%s must be at least %d" % (where, key, low))
    # The verdicts it reads are kept 21 days, and one is the recent day.
    days = alert.get("window_days")
    if isinstance(days, int) and not 1 <= days <= 20:
        problems.append("%s.window_days must be 1 to 20" % where)
    if isinstance(alert.get("labels"), list) and not all(
            isinstance(label, str) and label for label in alert["labels"]):
        problems.append("%s.labels must be label names" % where)
    owner = alert.get("owner")
    if isinstance(owner, str) and owner not in data.get("agents", {}):
        problems.append("%s.owner %r is not an agent" % (where, owner))


def _check_checks(checks, data, problems, where="checks"):
    """The `checks` section, or a block of `checks.instances`, whose
    problems then name it as `where`."""
    for key, kinds in CHECKS.items():
        if key in checks and not _is(checks[key], kinds):
            problems.append("%s.%s must be %s" % (where, key, _type_name(kinds)))
    for key in ("slots", "step_timeout_minutes", "depends_minutes",
                "max_wait_minutes"):
        if _is(checks.get(key), int) and checks[key] < 1:
            problems.append("%s.%s must be at least 1" % (where, key))
    if _is(checks.get("cpu_weight"), int) \
            and not 0 <= checks["cpu_weight"] <= 10000:
        problems.append("%s.cpu_weight must be 0 (none) or 1 to 10000" % where)
    if isinstance(checks.get("fast_lane"), dict):
        _check_fast_lane(checks, problems, where)
    if isinstance(checks.get("reuse"), dict):
        _check_reuse(checks, problems, where)
    if isinstance(checks.get("fresh"), dict):
        _check_fresh(checks, problems, where)
    if isinstance(checks.get("main"), str) \
            and checks["main"] not in MAIN_MODES:
        problems.append("%s.main must be one of %s"
                        % (where, ", ".join(MAIN_MODES)))
    if isinstance(checks.get("main_red_issue"), dict):
        _fields(where + ".main_red_issue", checks["main_red_issue"],
                MAIN_RED_ISSUE, problems)
        if checks.get("main", "needed") != "every" \
                and not checks.get("depends"):
            problems.append("checks.main_red_issue needs checks.main "
                            "\"every\" (or checks.depends): otherwise the "
                            "default branch is never checked")
    if isinstance(checks.get("step_alert"), dict):
        _check_step_alert(checks["step_alert"], data, problems,
                          where + ".step_alert")
    if isinstance(checks.get("keep_ignored"), list):
        for path in checks["keep_ignored"]:
            if not (isinstance(path, str) and path.strip("/")
                    and ".." not in path.split("/")
                    and not any(c in path for c in "*?[!\\")):
                problems.append("%s.keep_ignored: %r is not a path "
                                "inside the worktree" % (where, path))
    if isinstance(checks.get("drop_env"), list):
        for entry in checks["drop_env"]:
            if not isinstance(entry, str) or not DROP_ENV_RE.match(entry):
                problems.append("%s.drop_env: %r is not a variable's "
                                "name or a prefix ending in *" % (where, entry))
    if isinstance(checks.get("host_fault_text"), list):
        for text in checks["host_fault_text"]:
            if not isinstance(text, str) or not text.strip():
                problems.append("%s.host_fault_text: %r is not a non-empty "
                                "string" % (where, text))
    if isinstance(checks.get("first_authors"), list):
        agents = data.get("agents", {})
        for name in checks["first_authors"]:
            if not isinstance(name, str) or name not in agents:
                problems.append("%s.first_authors: %r is not an agent"
                                % (where, name))
    if isinstance(checks.get("cache_limit"), str) \
            and not SIZE_RE.match(checks["cache_limit"]):
        problems.append("%s.cache_limit must be a size such as 40G "
                        "(K, M, G or T)" % where)
    if isinstance(checks.get("tracer"), str) \
            and checks["tracer"] not in TRACERS:
        problems.append("%s.tracer must be one of %s"
                        % (where, ", ".join(TRACERS)))
    if isinstance(checks.get("steps"), list):
        for i, step in enumerate(checks["steps"]):
            at = "%s.steps[%d]" % (where, i)
            if not _fields(at, step, STEP, problems):
                continue
            if "name" not in step:
                problems.append("%s: missing 'name'" % at)
            run = step.get("run")
            if not (isinstance(run, list) and run
                    and all(isinstance(a, str) for a in run)):
                problems.append("%s.run must be a non-empty list of strings"
                                % at)
            version = step.get("version")
            if "version" in step and not (
                    isinstance(version, list) and version
                    and all(isinstance(a, str) for a in version)):
                problems.append("%s.version must be a non-empty list of "
                                "strings" % at)
            for path in step.get("outputs", []) \
                    if isinstance(step.get("outputs"), list) else ():
                if not (isinstance(path, str) and path.strip("/")
                        and ".." not in path.split("/")
                        and not any(c in path for c in "*?[!\\")):
                    problems.append("%s.outputs: %r is not a path inside "
                                    "the worktree" % (at, path))
    if isinstance(checks.get("caches"), dict) and not all(
            isinstance(v, str) and v and "/" not in v
            for v in checks["caches"].values()):
        problems.append("%s.caches must map variables to directory names" % where)
    poster = checks.get("poster")
    if isinstance(poster, str) and poster not in data.get("machinery", {}) \
            and poster not in data.get("agents", {}):
        problems.append("%s.poster %r is no App in the configuration"
                        % (where, poster))


def _check_instances(instances, data, problems):
    """checks.instances (designs/0021): each block is a `checks` section
    of its own, with `context` and `steps` required, `host` a string,
    the Merger's keys refused and any key that isn't a check's refused
    too, so that a typo can't pass silently."""
    for name, block in instances.items():
        where = "checks.instances.%s" % name
        if not INSTANCE_NAME_RE.match(name):
            problems.append("%s: an instance's name is lower-case letters, "
                            "digits and hyphens" % where)
        if not isinstance(block, dict):
            problems.append("%s must be an object" % where)
            continue
        for key in block:
            if key.startswith("_"):
                continue
            if key in INSTANCE_CHECKS_ONLY:
                problems.append("%s.%s: only checks may set it, since it "
                                "belongs to the context the Merger requires"
                                % (where, key))
            elif key not in INSTANCE_OWN + INSTANCE_INHERITED \
                    + INSTANCE_BLOCK_ONLY:
                problems.append("%s: unknown key %r" % (where, key))
        for key in ("context", "steps"):
            if key not in block:
                problems.append("%s: missing %r" % (where, key))
        if "host" in block and not isinstance(block["host"], str):
            problems.append("%s.host must be a string" % where)
        own = {k: v for k, v in block.items()
               if k in INSTANCE_OWN + INSTANCE_INHERITED + INSTANCE_BLOCK_ONLY
               and k != "host"}
        _check_checks(own, data, problems, where)


def instance_checks(data, name):
    """The `checks` section that instance `name` runs with, and its
    host: the block's own keys, then what it inherits from `checks`,
    with poster and host falling back to checks.poster and the
    top-level host. The tracer's keys and the Merger's keys aren't
    inherited. KeyError, listing the names, for an unknown instance."""
    checks = data.get("checks", {})
    instances = checks.get("instances", {})
    if name not in instances:
        raise KeyError("no checks.instances.%s (instances: %s)"
                       % (name, ", ".join(sorted(instances)) or "none"))
    block = instances[name]
    section = {k: checks[k] for k in INSTANCE_INHERITED if k in checks}
    if "poster" in checks:
        section["poster"] = checks["poster"]
    section.update({k: v for k, v in block.items()
                    if k != "host" and not k.startswith("_")})
    return section, block.get("host", data.get("host", ""))


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


def _check_fast_lane(checks, problems, section="checks"):
    """checks.fast_lane: a slot of its own for heads whose chosen steps
    have taken at most max_seconds here. Only `depends` chooses steps,
    so without it nothing could be cheap."""
    where = section + ".fast_lane"
    lane = checks["fast_lane"]
    _fields(where, lane, FAST_LANE, problems)
    if _is(lane.get("max_seconds"), int) and lane["max_seconds"] < 1:
        problems.append("%s.max_seconds must be at least 1" % where)
    if checks.get("depends") is not True:
        problems.append("%s needs %s.depends: without it every head "
                        "runs every step, and none is cheap" % (where, section))


def _check_fresh(checks, problems, section="checks"):
    """checks.fresh (designs/0010): dependency records from every green
    run, and how long a head waits for a fresh one. Records are what
    `depends` writes, so it needs it."""
    where = section + ".fresh"
    fresh = checks["fresh"]
    _fields(where, fresh, FRESH, problems)
    if _is(fresh.get("wait_minutes"), int) and fresh["wait_minutes"] < 1:
        problems.append("%s.wait_minutes must be at least 1" % where)
    if checks.get("depends") is not True:
        problems.append("%s needs %s.depends: it keeps the dependency "
                        "records depends writes fresh" % (where, section))


def _check_reuse(checks, problems, section="checks"):
    """checks.reuse (designs/0009): how old a step's green verdict may
    be to stand for unchanged inputs. The inputs are read from the
    dependency record, so it needs `depends`. Its switches
    (designs/0018) are booleans; `trace_matched_main` needs `main`
    "every", the only mode that matches a default-branch head."""
    where = section + ".reuse"
    reuse = checks["reuse"]
    _fields(where, reuse, REUSE, problems)
    if _is(reuse.get("max_age_days"), int) and reuse["max_age_days"] < 1:
        problems.append("%s.max_age_days must be at least 1" % where)
    if checks.get("depends") is not True:
        problems.append("%s needs %s.depends: a step's inputs are "
                        "what its dependency record says it read" % (where, section))
    if reuse.get("trace_matched_main") is True \
            and checks.get("main", "needed") != "every":
        problems.append("%s.trace_matched_main needs %s.main \"every\": "
                        "only then is a default-branch head matched"
                        % (where, section))


def _check_idle_offer(offer, agents, problems):
    """router.idle_offer: how long an agent is idle before it is offered
    its next item, and who is told of a developer with none."""
    where = "router.idle_offer"
    _fields(where, offer, IDLE_OFFER, problems)
    for key in ("idle_minutes", "shell_minutes"):
        if _is(offer.get(key), int) and offer[key] < 1:
            problems.append("%s.%s must be at least 1" % (where, key))
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


def _check_unreviewed(gaps, router, problems):
    """router.unreviewed (designs/0027): which paths no reviewer need
    cover, and which directories' areas go two segments deep. Its
    issues are the triage owner's, so it needs one."""
    where = "router.unreviewed"
    _fields(where, gaps, UNREVIEWED, problems)
    for key in ("ignore", "depth2"):
        values = gaps.get(key)
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, str) or not value:
                problems.append("%s.%s: each must be a non-empty string"
                                % (where, key))
                break
            if key == "depth2" and "/" in value:
                problems.append("%s.depth2: %r must be a top-level "
                                "directory, with no /" % (where, value))
    owners = router.get("owners")
    if not isinstance(owners, dict) or not owners.get("triage"):
        problems.append("%s needs router.owners.triage: its issues are "
                        "the triage owner's" % where)


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

    def sessions(self, name):
        """Every session that works as the agent: hers, then her twins'
        (sessions that share her App, such as one on a second host or
        account), in the configuration's order."""
        return [self.session_name(name)] \
            + list(self.agents[name].get("twins", []))

    def stand_in(self, name):
        """The agent who works for `name`: her `away` stand-in, else
        herself."""
        return self.agents.get(name, {}).get("away") or name

    def by_session(self):
        """{session name: agent} for every agent's sessions, twins
        included."""
        return {s: a for a in self.agents for s in self.sessions(a)}

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
