"""A simulation of the Merger at work, for tests (docs/merger-sim.md).

The real `bin/forge-merger` `tick` runs, live, minute after simulated
minute, against:

- real git: a bare `origin` and the Merger's own checkout, so every
  tree, merge and conflict is git's own;
- `SimGitHub`, which answers what the Merger asks and does what it
  writes the way GitHub does where it matters: `update-branch` makes a
  new merge commit (a new head, with the tree the Merger predicted), a
  squash merge lands one commit on the default branch, a merged
  branch's stacked pull requests are retargeted, statuses are kept per
  commit;
- `SimCheck`, a model of the build check (`forge-pr-check`): it runs
  the Merger's batch tops and the heads no batch covers, in a few
  slots, gives each verdict after some minutes, matches and covers
  heads from green runs as the build check does, and runs a gated
  instance (`vm/mac`) in a slower queue of its own;
- a scenario: pull requests opened, approved, stacked, pushed to,
  marked urgent, some of them red, at chosen minutes.

`Monitor` checks the rules a merge queue must keep after every tick,
and `Sim.run` returns what it found with the run's figures. Nothing
reaches the network.
"""

import collections
import datetime
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile

from agent_forge import config
from tests.helpers import ROOT, example

SLUG = "example/widget"
OWNER = "example"
REVIEWER = "reviewerwidgetagent"
AUTHOR = "leaddevwidgetagent"
POSTER = "mergerwidgetagent[bot]"
CONTEXT = "vm/build-and-test"
GATED = "vm/mac"
URGENT = "Urgent"
START = datetime.datetime(2026, 10, 7, 8, 0, 0, tzinfo=datetime.timezone.utc)


def load_merger(source=None):
    """bin/forge-merger as a module, or `source`, the text of another
    version of it (as `git show <tag>:bin/forge-merger` gives it)."""
    path = ROOT / "bin" / "forge-merger"
    if source is not None:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix="-forge-merger", delete=False)
        handle.write(source)
        handle.close()
        path = handle.name
    loader = importlib.machinery.SourceFileLoader(
        "forge_merger_sim_%d" % id(source), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    if source is not None:
        # Its tools are this installation's.
        module.BIN = str(ROOT / "bin")
        os.unlink(path)
    return module


def merger_source(tag):
    """bin/forge-merger as it was at `tag`, or None when git can't say."""
    got = subprocess.run(["git", "-C", str(ROOT), "show",
                          "%s:bin/forge-merger" % tag],
                         capture_output=True, text=True)
    return got.stdout if got.returncode == 0 else None


def project(**ahead):
    """The configuration the simulation runs the Merger with: batches,
    trains, stacks and a gated instance, as a busy project has them."""
    data = example()
    data["merge"] = {
        "merger": "Merger", "ci_workflow": "", "stall_minutes": 600,
        "gate": [], "own_head_green": True, "update_once": True,
        "test_ahead": dict({
            "depth": 12, "batch": 4, "batches": 2, "trains": "live",
            "stacks": True, "stop_spent": True, "green_batch_first": True,
            "retry_red": True, "max_age_hours": 24}, **ahead),
        "stacks": {"wait_hours": 24, "predict_restacked": True},
    }
    data.setdefault("labels", {})["urgent"] = URGENT
    data["checks"]["instances"] = {"mac": {
        "context": GATED, "poster": "Merger", "gate": True,
        "steps": [{"name": "t", "run": ["true"]}],
        "pull_requests": {"paths": ["mac/*"]}}}
    return config.Project(data, None)


def git_env():
    return dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull)


def iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class Git:
    """git in one repository, raising on failure unless `check` is off."""

    def __init__(self, path):
        self.path = path

    def __call__(self, *args, check=True, env=None, input=None):
        got = subprocess.run(["git", "-C", self.path] + list(args),
                             capture_output=True, text=True,
                             env=dict(git_env(), **(env or {})), input=input)
        if check and got.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), got.stderr))
        return got.stdout.strip()


class World:
    """origin, the Merger's checkout, the pull requests and GitHub's
    state of them. Pull requests are dictionaries keyed by number:
    `base`, `branch`, `head`, `version` (the author's content, bumped
    by each push of theirs), `approved` (the version the reviewer last
    approved), `mac` (whether it changes a path the gated instance
    runs on), `red` (whether its change breaks the build), `state`."""

    def __init__(self, root):
        self.root = root
        self.origin = os.path.join(root, "origin.git")
        self.work = os.path.join(root, "work")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        self.origin], check=True, env=git_env())
        subprocess.run(["git", "init", "-q", "-b", "main", self.work],
                       check=True, env=git_env())
        self.og = Git(self.origin)
        self.wg = Git(self.work)
        self.wg("remote", "add", "origin", self.origin)
        # The Merger makes its predictions in its own checkout; the
        # build check reads their trees from origin, as a host sharing
        # the objects would.
        with open(os.path.join(self.origin, "objects", "info",
                               "alternates"), "a") as handle:
            handle.write(os.path.join(self.work, ".git", "objects") + "\n")
        tree = self.write_tree(None, {"base.txt": "base\n"})
        sha = self.og("commit-tree", tree, "-m", "base")
        self.og("update-ref", "refs/heads/main", sha)
        self.wg("fetch", "-q", "origin")
        self.prs = {}
        self.statuses = collections.defaultdict(list)
        self.issues = set()
        self.ids = 0
        self.now = START
        self.events = []
        self.merges = []
        self.auto_retarget = True

    # git plumbing in origin

    def write_tree(self, parent_tree, files):
        """A tree: `parent_tree` (or empty) with `files` written."""
        index = os.path.join(self.root, "index.tmp")
        env = {"GIT_INDEX_FILE": index}
        if os.path.exists(index):
            os.remove(index)
        if parent_tree:
            self.og("read-tree", parent_tree, env=env)
        for name, text in files.items():
            blob = self.og("hash-object", "-w", "--stdin", input=text)
            self.og("update-index", "--add", "--cacheinfo",
                    "100644,%s,%s" % (blob, name), env=env)
        return self.og("write-tree", env=env)

    def tree(self, sha):
        return self.og("rev-parse", sha + "^{tree}")

    def main(self):
        return self.og("rev-parse", "refs/heads/main")

    def ref(self, name):
        return self.og("rev-parse", "--verify", "-q", name, check=False)

    def needs_gate(self, number):
        """Whether #`number` changes a path the gated instance runs on,
        since it left the default branch (as the Merger asks it)."""
        pr = self.prs[number]
        base = self.og("merge-base", self.main(), pr["head"], check=False)
        if not base:
            return True
        return any(path.startswith("mac/") for path in self.og(
            "diff", "--name-only", base, pr["head"]).split())

    def gate_verdict(self, number):
        """The gated instance's verdict for #`number` as the Merger reads
        it: on the head, or, while that has none final, on the commit
        before each merge of the default branch into it."""
        sha, first = self.prs[number]["head"], None
        for _ in range(20):
            state = next((s["state"] for s in self.statuses.get(sha, ())
                          if s["context"] == GATED), None)
            if state not in (None, "pending"):
                return state
            first = first or state
            parents = self.og("rev-list", "--parents", "-n", "1",
                              sha).split()
            if len(parents) != 3:
                break
            got = subprocess.run(
                ["git", "-C", self.origin, "merge-base", "--is-ancestor",
                 parents[2], self.main()], env=git_env())
            if got.returncode != 0:
                break
            sha = parents[1]
        return first

    def files(self, tree):
        return self.og("ls-tree", "-r", "--name-only", tree).split()

    def commit(self, parent, files, message):
        tree = self.write_tree(self.tree(parent), files)
        return self.og("commit-tree", tree, "-p", parent, "-m", message)

    def set_head(self, number, sha):
        pr = self.prs[number]
        self.og("update-ref", "refs/heads/" + pr["branch"], sha)
        self.og("update-ref", "refs/pull/%d/head" % number, sha)
        pr["head"] = sha

    def merge_tree(self, ours, theirs):
        got = subprocess.run(
            ["git", "-C", self.origin, "merge-tree", "--write-tree",
             "--no-messages", ours, theirs], capture_output=True, text=True,
            env=git_env())
        if got.returncode != 0:
            return None
        return got.stdout.split()[0]

    # the authors and the reviewer

    def note(self, what, **fields):
        self.events.append(dict(fields, at=iso(self.now), what=what))

    def review(self, number):
        pr = self.prs[number]
        self.ids += 1
        pr["reviews"].append({
            "databaseId": self.ids, "author": {"login": REVIEWER},
            "authorCanPushToRepository": True, "state": "APPROVED",
            "createdAt": iso(self.now), "submittedAt": iso(self.now),
            "body": "", "commit": {"oid": pr["head"]},
            "comments": {"totalCount": 0}})
        pr["approved"] = pr["version"]
        self.note("approve", pr=number)

    def open(self, number, base=None, mac=False, red=False, urgent=False,
             approve=True):
        """Opens #`number`, on main or on the pull request `base`, with a
        change of its own (a file of its own, so nothing conflicts)."""
        branch = "b%d" % number
        start = self.main() if base is None else self.prs[base]["head"]
        files = {"p%d.txt" % number: "v1\n"}
        if mac:
            files["mac/p%d.txt" % number] = "v1\n"
        if red:
            files["red-%d.txt" % number] = "breaks the build\n"
        sha = self.commit(start, files, "#%d" % number)
        self.prs[number] = {
            "number": number, "created": iso(self.now),
            "base": "main" if base is None else self.prs[base]["branch"],
            "branch": branch, "head": sha, "version": 1, "approved": None,
            "mac": mac, "red": red, "state": "OPEN", "reviews": [],
            "labels": [URGENT] if urgent else [], "comments": []}
        self.set_head(number, sha)
        self.note("open", pr=number, base=base, mac=mac, red=red,
                  urgent=urgent)
        if approve:
            self.review(number)

    def push(self, number):
        """The author pushes a change of their own to #`number`."""
        pr = self.prs[number]
        pr["version"] += 1
        sha = self.commit(pr["head"], {"p%d.txt" % number: "v%d\n"
                                       % pr["version"]}, "push")
        self.set_head(number, sha)
        self.note("push", pr=number)

    def sync(self):
        """GitHub follows a branch pushed to: a pull request's head is
        its branch's tip (the Merger pushes some merges itself)."""
        for number, pr in self.prs.items():
            if pr["state"] != "OPEN":
                continue
            tip = self.ref("refs/heads/" + pr["branch"])
            if tip and tip != pr["head"]:
                self.set_head(number, tip)


class SimGitHub:
    """What the Merger reads from and writes to GitHub, over `world`."""

    def __init__(self, world):
        self.world = world
        self.env = {"GH_TOKEN": "fake"}
        self.writes = []

    def prefetch(self, paths, workers=8):
        list(paths)

    def node(self, pr):
        return {
            "number": pr["number"], "title": "#%d" % pr["number"],
            "createdAt": pr["created"], "lastEditedAt": None,
            "isDraft": False, "baseRefName": pr["base"],
            "headRefName": pr["branch"], "headRefOid": pr["head"],
            "body": "", "author": {"login": AUTHOR},
            "labels": {"nodes": [{"name": n} for n in pr["labels"]]},
            "reviews": {"nodes": list(pr["reviews"])},
            "comments": {"nodes": list(pr["comments"])}}

    def api(self, *args):
        w = self.world
        path = args[1] if args[0] == "--paginate" else args[0]
        if path == "graphql":
            if any("closingIssuesReferences" in a for a in args):
                return {"data": {"repository": {"pullRequest": {
                    "closingIssuesReferences": {"nodes": []}}}}}
            return {"data": {"repository": {"pullRequests": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [self.node(pr) for pr in w.prs.values()
                          if pr["state"] == "OPEN"]}}}}
        prefix = "repos/%s/" % SLUG
        if not path.startswith(prefix):
            raise AssertionError("unexpected gh api %r" % (args,))
        rest = path[len(prefix):]
        if rest.startswith("issues?"):
            return sorted(w.issues)
        if rest.startswith("commits/"):
            sha = rest.split("/")[1]
            if "/check-runs" in rest:
                return {"check_runs": []}
            return list(w.statuses.get(sha, []))
        if rest.startswith("actions/runs"):
            return {"workflow_runs": []}
        if rest.startswith("pulls?state=closed"):
            branch = rest.split("head=")[1].split("&")[0].split(":", 1)[1]
            return [{"number": pr["number"], "merged_at": pr["merged_at"],
                     "base": {"ref": "main"},
                     "head": {"sha": pr["head"], "ref": pr["branch"]}}
                    for pr in w.prs.values()
                    if pr["branch"] == branch and pr.get("merged_at")]
        if rest.startswith("pulls/"):
            pr = w.prs[int(rest.split("/")[1])]
            return {"head": {"sha": pr["head"], "ref": pr["branch"],
                             "repo": {"full_name": SLUG}},
                    "base": {"ref": pr["base"]}}
        if rest.startswith("git/ref/heads/"):
            return {"object": {"sha": w.ref("refs/heads/" + rest[14:])}}
        raise AssertionError("unexpected gh api %r" % (args,))

    def create(self, path, **fields):
        ok, why = self.write("POST", path, **fields)
        number = 9000 + len(self.world.issues)
        if ok:
            self.world.issues.add(number)
        return (number, "") if ok else (None, why)

    def write(self, method, path, **fields):
        self.writes.append((method, path, fields))
        w = self.world
        rest = path[len("repos/%s/" % SLUG):]
        parts = rest.split("/")
        if parts[0] == "pulls" and len(parts) == 3:
            number, pr = int(parts[1]), w.prs[int(parts[1])]
            if parts[2] == "update-branch":
                return self.update_branch(number, pr,
                                          fields.get("expected_head_sha"))
            if parts[2] == "merge":
                return self.merge(number, pr, fields.get("sha"))
        if parts[0] == "pulls" and len(parts) == 2 and method == "PATCH":
            pr = w.prs[int(parts[1])]
            if fields.get("base") and fields["base"] != pr["base"]:
                pr["base"] = fields["base"]
                w.note("retarget", pr=pr["number"], by="merger")
            return True, ""
        if parts[0] == "merges":
            return self.restack(fields["base"], fields["head"])
        return True, ""

    def update_branch(self, number, pr, expected):
        w = self.world
        if expected and expected != pr["head"]:
            return False, "expected head sha didn't match current head ref"
        main = w.main()
        tree = w.merge_tree(pr["head"], main)
        if tree is None:
            return False, "merge conflict"
        sha = w.og("commit-tree", tree, "-p", pr["head"], "-p", main, "-m",
                   "Merge branch 'main' into %s" % pr["branch"])
        w.set_head(number, sha)
        w.note("update-branch", pr=number)
        return True, ""

    def restack(self, branch, lower):
        w = self.world
        number = next(n for n, p in w.prs.items() if p["branch"] == branch
                      and p["state"] == "OPEN")
        pr = w.prs[number]
        tree = w.merge_tree(pr["head"], lower)
        if tree is None:
            return False, "merge conflict"
        sha = w.og("commit-tree", tree, "-p", pr["head"], "-p", lower, "-m",
                   "restack")
        w.set_head(number, sha)
        w.note("restack", pr=number)
        return True, ""

    def merge(self, number, pr, sha):
        w = self.world
        if sha != pr["head"]:
            return False, "Head branch was modified"
        main = w.main()
        tree = w.merge_tree(main, pr["head"])
        if tree is None:
            return False, "Pull Request is not mergeable"
        if pr["base"] != "main":
            return False, "Base is not main"
        squash = w.og("commit-tree", tree, "-p", main, "-m",
                      "#%d" % number)
        w.og("update-ref", "refs/heads/main", squash, main)
        pr["state"] = "MERGED"
        pr["merged_at"] = iso(w.now)
        w.merges.append({"pr": number, "tree": tree, "at": iso(w.now),
                         "head": pr["head"]})
        w.note("merge", pr=number)
        if w.auto_retarget:
            for other in w.prs.values():
                if other["state"] == "OPEN" and other["base"] == pr["branch"]:
                    other["base"] = "main"
                    w.note("retarget", pr=other["number"], by="github")
        return True, ""


class SimCheck:
    """A model of the build check: a few slots, a verdict after
    `minutes`, the Merger's tops first, heads matched or covered by a
    green run as forge-pr-check does, and the gated instance in a queue
    of its own with `mac_slots` slots taking `mac_minutes`."""

    def __init__(self, world, merger_dir, slots=4, minutes=8,
                 mac_slots=1, mac_minutes=12, notice=2):
        self.world = world
        self.merger_dir = merger_dir
        self.slots, self.minutes = slots, minutes
        self.mac_slots, self.mac_minutes = mac_slots, mac_minutes
        self.state = {"results": {}, "ahead_started": {}}
        self.running = []
        self.queue = []
        self.mac_running = []
        self.mac_queue = []
        self.mac_done = {}
        self.posted = {}
        # A head is acted on `notice` minutes after it is first seen:
        # the build check runs on its own minute, and a member's
        # "covered by batch" green comes a minute or two after the
        # Merger's update made the head.
        self.notice = datetime.timedelta(minutes=notice)
        self.seen = {}
        self.runs = 0
        self.top_runs = 0

    def post(self, sha, state, description, context=CONTEXT):
        w = self.world
        key = (sha, context)
        if self.posted.get(key) == (state, description):
            return
        self.posted[key] = (state, description)
        w.statuses[sha].insert(0, {
            "context": context, "state": state, "description": description,
            "creator": {"login": POSTER}})

    def ahead(self):
        try:
            with open(os.path.join(self.merger_dir, "ahead.json")) as handle:
                said = json.load(handle)
        except (OSError, ValueError):
            return [], []
        entries = said.get("entries") or []
        batch = said.get("batch") or {}
        size = batch.get("size") or 0
        if size < 1 or [e.get("tree") for e in entries[:size]] \
                != batch.get("trees"):
            return [], []
        # As forge-pr-check's merger_batch: a batch of one is no batch,
        # unless it is a red batch's suspects or later batches follow.
        least = 1 if batch.get("suspects") is True or said.get("later") \
            else 2
        if size < least:
            return entries, []
        batches, at = [entries[:size]], size
        for later in said.get("later") or []:
            n = later.get("size") or 0
            if n < 1 or [e.get("tree") for e in entries[at:at + n]] \
                    != later.get("trees"):
                break
            batches.append(entries[at:at + n])
            at += n
        return entries, batches

    def verdict(self, tree):
        return not any(name.startswith("red-")
                       for name in self.world.files(tree))

    def green(self, tree):
        return any(r.get("ok") is True and r.get("tree") == tree
                   and not r.get("covered")
                   for r in self.state["results"].values())

    def busy(self, sha):
        return any(r["sha"] == sha for r in self.running + self.queue)

    def step(self):
        w = self.world
        now = w.now
        for run in [r for r in self.running if r["done"] <= now]:
            self.running.remove(run)
            ok = self.verdict(run["tree"])
            self.state["results"][run["sha"]] = {
                "tree": run["tree"], "ok": ok, "pr": run["pr"],
                "ahead": run["top"], "finished": iso(now)}
            if not run["top"]:
                self.post(run["sha"], "success" if ok else "failure",
                          "green" if ok else "red: test")
        entries, batches = self.ahead()
        for batch in batches:
            top = batch[-1]
            if top["sha"] not in self.state["results"] \
                    and not self.busy(top["sha"]):
                self.queue.insert(sum(1 for r in self.queue if r["top"]),
                                  {"sha": top["sha"], "tree": top["tree"],
                                   "pr": top["pr"], "top": True})
        self.heads(batches)
        while self.queue and len(self.running) < self.slots:
            run = self.queue.pop(0)
            run["done"] = now + datetime.timedelta(minutes=self.minutes)
            self.running.append(run)
            self.runs += 1
            if run["top"]:
                self.top_runs += 1
                self.state["ahead_started"][run["sha"]] = iso(now)
        self.mac()

    def heads(self, batches):
        w = self.world
        for pr in w.prs.values():
            if pr["state"] != "OPEN":
                continue
            sha = pr["head"]
            if sha in self.state["results"] or self.busy(sha):
                continue
            if w.now - self.seen.setdefault(sha, w.now) < self.notice:
                continue
            tree = w.tree(sha)
            same = [s for s, r in self.state["results"].items()
                    if r.get("tree") == tree and r.get("ok") is True
                    and not r.get("covered")]
            if same:
                self.state["results"][sha] = {"tree": tree, "ok": True,
                                              "matched": same[0]}
                self.post(sha, "success", "green: matched %s" % same[0][:10])
                continue
            held = False
            for batch in batches:
                trees = [e["tree"] for e in batch]
                if tree not in trees:
                    continue
                top = batch[-1]
                span = "#%d..#%d" % (batch[0]["pr"], top["pr"])
                if self.green(top["tree"]):
                    self.state["results"][sha] = {
                        "tree": tree, "ok": True, "covered": span}
                    self.post(sha, "success",
                              "green: covered by batch %s (tested as %s)"
                              % (span, top["sha"][:10]))
                else:
                    self.post(sha, "pending", "held for the Merger's batch "
                              "%s" % span)
                held = True
                break
            if held:
                continue
            self.post(sha, "pending", "queued")
            self.queue.append({"sha": sha, "tree": tree, "pr": pr["number"],
                               "top": False})

    def mac(self):
        """The gated instance: one run per pull request's own content
        (a merge of main into it changes none of it), posted on the head
        it ran on; the Merger looks back past merges of main for it."""
        w = self.world
        now = w.now
        for run in [r for r in self.mac_running if r["done"] <= now]:
            self.mac_running.remove(run)
            self.mac_done[run["sha"]] = True
            self.post(run["sha"], "success", "green", GATED)
        for pr in w.prs.values():
            if pr["state"] != "OPEN" or not w.needs_gate(pr["number"]):
                continue
            if w.gate_verdict(pr["number"]) not in (None, "pending") or any(
                    r["sha"] == pr["head"]
                    for r in self.mac_running + self.mac_queue):
                continue
            self.post(pr["head"], "pending", "queued", GATED)
            self.mac_queue.append({"pr": pr["number"], "sha": pr["head"],
                                   "version": pr["version"]})
        while self.mac_queue and len(self.mac_running) < self.mac_slots:
            run = self.mac_queue.pop(0)
            run["done"] = now + datetime.timedelta(minutes=self.mac_minutes)
            self.mac_running.append(run)


class Monitor:
    """The rules a merge queue keeps, checked after every tick. Each
    break is a line in `broken`, with the minute and what the Merger did.

    1. A pull request leaves a started train only with it merged at the
       tree predicted for its place, or because the train's top was red
       (the search for the culprit), or because the scenario did
       something to it or to one ahead of it (its author pushed, it was
       re-approved) — never through the Merger's own doing.
    2. The default branch only ever takes a tree a green run tested:
       its own, or a prefix of a train whose top was green.
    3. No pull request boards a train without an approval of its
       author's latest change, nor without the gated instance's green
       on that change when it needs one.
    4. Nothing red ever reaches the default branch.
    5. A train whose top has started merges in its own order: no member
       merges while one before it in the train is still unmerged (on
       2026-11-11 a member merged on its own green ahead of an urgent
       one cut in front of it, and the green train's prediction no
       longer held)."""

    def __init__(self, sim):
        self.sim = sim
        self.trains = {}
        self.broken = []
        self.left = 0
        self.seen_merges = 0
        self.boarded = set()
        self.batches = {}
        self.recuts = []

    def say(self, rule, text):
        line = "%s rule %d: %s" % (iso(self.sim.world.now)[11:16], rule, text)
        if line not in self.broken:
            self.broken.append(line)

    def touched(self, number, since):
        return any(e.get("pr") == number and e["what"] in ("push", "approve")
                   and e["at"] > since for e in self.sim.world.events)

    def why(self, number):
        """What the Merger did to or said about #`number` this tick or
        the one before, and its output lines this minute."""
        said = []
        for record in self.sim.records[-2:]:
            for d in record.get("decisions", ()):
                if d.get("pr") == number:
                    said.append("%s %s" % (record["at"][11:16],
                                           d["action"]))
            held = (record.get("held") or {}).get(str(number))
            if held:
                said.append("%s held: %s" % (record["at"][11:16], held))
        minute = iso(self.sim.world.now)[11:16]
        said += [l for l in self.sim.lines if l.startswith(minute)
                 and "TICK" not in l]
        return "; ".join(said) or "nothing said"

    def check(self):
        w, check = self.sim.world, self.sim.check
        entries, batches = check.ahead()
        # Still predicted at the tree its train tested: aboard, though a
        # last member alone is no batch to the build check.
        aboard = {(e["pr"], e["tree"]) for e in entries}
        # Rule 3: who boards.
        for batch in batches:
            for e in batch:
                pr = w.prs.get(e["pr"])
                if pr is None or (e["pr"], pr["head"]) in self.boarded:
                    continue
                self.boarded.add((e["pr"], pr["head"]))
                if pr["approved"] != pr["version"]:
                    self.say(3, "#%d boarded without an approval of its "
                             "latest change" % e["pr"])
                if w.needs_gate(e["pr"]) \
                        and w.gate_verdict(e["pr"]) != "success":
                    self.say(3, "#%d boarded with %s not yet green on its "
                             "change" % (e["pr"], GATED))
        # Every batch the Merger predicted: a green top covers its
        # prefixes, whoever's run found its tree green.
        for batch in batches:
            self.batches[batch[-1]["tree"]] = [e["tree"] for e in batch]
        # Trains are tracked from the minute their top starts.
        for batch in batches:
            top = batch[-1]
            if top["sha"] in check.state["ahead_started"] \
                    and top["sha"] not in self.trains:
                self.trains[top["sha"]] = {
                    "members": [(e["pr"], e["tree"]) for e in batch],
                    "since": check.state["ahead_started"][top["sha"]],
                    "tree": top["tree"], "done": False}
        # Rule 5: a started train merges in its own order.
        before = {m["pr"] for m in w.merges[:self.seen_merges]}
        for m in w.merges[self.seen_merges:]:
            for train in self.trains.values():
                if train["done"]:
                    continue
                order = [n for n, _ in train["members"]]
                if m["pr"] not in order:
                    continue
                ahead = [n for n in order[:order.index(m["pr"])]
                         if n not in before]
                if ahead:
                    self.say(5, "#%d merged ahead of #%d, before it in the "
                             "train #%d..#%d (%s)" % (
                                 m["pr"], ahead[0], order[0], order[-1],
                                 self.why(m["pr"])))
            before.add(m["pr"])
        merged = {m["pr"]: m["tree"] for m in w.merges}
        for top, train in self.trains.items():
            if train["done"]:
                continue
            result = check.state["results"].get(top)
            red = result is not None and result.get("ok") is False
            for at, (number, tree) in enumerate(train["members"]):
                if merged.get(number) == tree:
                    continue
                if (number, tree) in aboard:
                    break
                train["done"] = True
                if red or any(self.touched(n, train["since"])
                              for n, _ in train["members"][:at + 1]):
                    break
                minute = iso(w.now)[11:16]
                if any(l.startswith(minute + " TRAIN-RECUT")
                       for l in self.sim.lines):
                    # An urgent pull request goes first by design, and
                    # the trains after it are cut again: counted, not a
                    # break.
                    self.recuts.append("%s #%d" % (minute, number))
                    break
                self.left += 1
                self.say(1, "#%d left the train #%d..#%d without merging "
                         "and through nothing done to it (%s)" % (
                             number, train["members"][0][0],
                             train["members"][-1][0], self.why(number)))
                break
            else:
                train["done"] = True
        # Rules 2 and 4: what the default branch took.
        for m in w.merges[self.seen_merges:]:
            if not check.verdict(m["tree"]):
                self.say(4, "#%d merged a red tree" % m["pr"])
            if not (check.green(m["tree"]) or self.prefix_green(m["tree"])):
                self.say(2, "#%d merged a tree no green run covered"
                         % m["pr"])
        self.seen_merges = len(w.merges)

    def prefix_green(self, tree):
        check = self.sim.check
        return any(tree in trees and check.green(top)
                   for top, trees in self.batches.items())


Result = collections.namedtuple("Result", [
    "broken", "merged", "hours", "runs", "top_runs", "left", "events",
    "lines", "recuts"])


class Sim:
    """The Merger, the build check and a scenario, minute by minute."""

    def __init__(self, merger, root, scenario, step=1, **check):
        self.m = merger
        self.root = root
        self.world = World(root)
        self.github = SimGitHub(self.world)
        self.state = os.path.join(root, "merger")
        os.makedirs(self.state)
        self.check = SimCheck(self.world, self.state, **check)
        self.scenario = sorted(scenario, key=lambda e: e[0])
        self.step = step
        self.records = []
        self.lines = []
        self.monitor = Monitor(self)

    def run_tools(self, args, cwd=None, env=None):
        return subprocess.run(args, cwd=cwd, env=dict(env or os.environ,
                                                      **git_env()),
                              capture_output=True, text=True)

    def run(self, minutes):
        w = self.world
        events = list(self.scenario)
        for minute in range(0, minutes, self.step):
            w.now = START + datetime.timedelta(minutes=minute)
            while events and events[0][0] <= minute:
                _, action, args = events.pop(0)
                getattr(w, action)(*args)
            self.check.step()
            record = self.m.tick(
                self.world.work, self.state, self.github, self.run_tools,
                lambda line: self.lines.append(
                    "%s %s" % (iso(w.now)[11:16], line)),
                now=lambda: w.now, sleep=lambda s: None, live=True,
                check_state=self.check.state)
            self.records.append(record)
            w.sync()
            self.check.step()
            self.monitor.check()
            if not events and all(p["state"] != "OPEN" or p["red"]
                                  for p in w.prs.values()):
                break
            if not events and w.merges and w.now - datetime.datetime.strptime(
                    w.merges[-1]["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=datetime.timezone.utc) \
                    > datetime.timedelta(minutes=60):
                break
        hours = max((w.now - START).total_seconds() / 3600, 1 / 60)
        return Result(self.monitor.broken, len(w.merges), hours,
                      self.check.runs, self.check.top_runs,
                      self.monitor.left, w.events, self.lines,
                      self.monitor.recuts)


def day(prs=14, seed=0):
    """A scenario: plain pull requests, a stack of three whose middle
    layer needs the gated instance, gated ones on main, an urgent one, a
    red one, and an author's push mid-train with the approval after it.
    [(minute, World method, args)]."""
    s = [(0, "open", (n,)) for n in range(1, 5)]
    s += [(0, "open", (5, None, True))]
    s += [(1, "open", (20,)), (1, "open", (21, 20, True)),
          (1, "open", (22, 21))]
    s += [(3, "open", (n,)) for n in range(6, 9)]
    s += [(4, "open", (9, None, False, False, True))]
    s += [(12, "open", (10, None, False, True))]
    s += [(14, "push", (7,)), (18, "review", (7,))]
    s += [(20 + 2 * k, "open", (n,)) for k, n in enumerate(range(11, prs))]
    return s


def busy(prs=40, seed=1, minutes=360):
    """A longer scenario from a seeded random source: pull requests
    arriving over `minutes`, a fifth of them gated, one in ten red, one
    in ten urgent, a stack of two to four layers every so often, and
    authors pushing to some after approval, approved again later."""
    import random
    rng = random.Random(seed)
    s, number, at = [], 1, 0
    while number <= prs:
        at += rng.randint(1, max(2, minutes // prs * 2))
        if rng.random() < 0.15 and number + 4 <= prs:
            layers = rng.randint(2, 4)
            base = None
            for _ in range(layers):
                s.append((at, "open", (number, base, rng.random() < 0.2)))
                base, number = number, number + 1
            continue
        red = rng.random() < 0.1
        s.append((at, "open", (number, None, rng.random() < 0.2, red,
                               rng.random() < 0.1)))
        if not red and rng.random() < 0.1:
            push = at + rng.randint(3, 20)
            s += [(push, "push", (number,)),
                  (push + rng.randint(2, 15), "review", (number,))]
        number += 1
    return s
