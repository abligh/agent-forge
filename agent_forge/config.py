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

CONFIG_NAME = os.path.join(".agent-forge", "config.json")
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
    "identity": (dict, False),
    "host_settings": (dict, False),
    "merge": (dict, False),
}
NAMING = {"app_slug_suffix": str, "session_prefix": str}
APP = {"app_id": (int, type(None)), "installation_id": (int, type(None)),
       "slug": str, "key": str}
AGENT = dict(APP, session_name=str, kind=str, reviews=list)
IDENTITY = {"unattributed_domain": str, "unattributed_name": str}
HOST_SETTINGS = {"files": list, "host_keys": list}
MERGE = {"merger": str, "ci_workflow": str, "gate": list,
         "stall_minutes": int}
# checks is shared with tools not here yet, so only these keys are
# checked.
CHECKS = {"context": str, "poster": str, "steps": list, "caches": dict,
          "slots": int, "memory_max": str, "step_timeout_minutes": int}
STEP = {"name": str, "run": list, "only_if": str}
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
    names = {str: "a string", int: "an integer", dict: "an object",
             list: "a list", type(None): "null"}
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
    checks = data.get("checks", {})
    for key, kinds in CHECKS.items():
        if key in checks and not _is(checks[key], kinds):
            problems.append("checks.%s must be %s" % (key, _type_name(kinds)))
    for key in ("slots", "step_timeout_minutes"):
        if _is(checks.get(key), int) and checks[key] < 1:
            problems.append("checks.%s must be at least 1" % key)
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
    if isinstance(checks.get("caches"), dict) and not all(
            isinstance(v, str) and v and "/" not in v
            for v in checks["caches"].values()):
        problems.append("checks.caches must map variables to directory names")
    poster = checks.get("poster")
    if isinstance(poster, str) and poster not in data.get("machinery", {}) \
            and poster not in data.get("agents", {}):
        problems.append("checks.poster %r is no App in the configuration"
                        % poster)
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
        """A free-form section (labels, checks, docs, relay), or {}."""
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
