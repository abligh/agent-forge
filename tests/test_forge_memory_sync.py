"""Tests for forge-memory-sync.

Real git, against a bare repository standing in for the memory
repository, with two memory directories standing in for two hosts.
Mocking git would test a model of git's rebase, and the properties that
matter here are git's: that `MEMORY.md` merges as a union, that an
aborted rebase leaves the working tree as it was, and that a refused
push is told apart from a failed one.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_forge import config, proc
from tests.helpers import ROOT, example, load_tool

sync = load_tool("forge-memory-sync")

# The user's own git configuration (a global hook path, signing, a
# credential store) must not reach these repositories.
ISOLATED = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0"}


def git(*args, cwd=None):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        *args], cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, **ISOLATED))
    if r.returncode != 0:
        raise AssertionError("git %s: %s" % (args, r.stderr))
    return r.stdout


def project(**section):
    data = example()
    data.pop("memory", None)
    if section:
        data["memory"] = section
    return config.Project(data, None)


class Fleet:
    """A bare remote and any number of hosts, each with its own paths."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.remote = self.tmp / "remote.git"
        git("init", "-q", "--bare", "-b", "main", str(self.remote))

    def env(self, host, **extra):
        base = self.tmp / host
        env = dict(os.environ, **ISOLATED)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GH_TOKEN",
                    "FORGE_AGENT"):
            env.pop(key, None)
        env.update({"FORGE_MEMORY_DIR": str(base / "memory"),
                    "FORGE_MEMORY_GIT_DIR": str(base / "memory.git"),
                    "FORGE_MEMORY_REMOTE": str(self.remote),
                    "FORGE_MEMORY_SEED_DIR": str(base / "seed"),
                    "FORGE_MEMORY_HOST": host,
                    "FORGE_STATE_DIR": str(base / "state")})
        env.update(extra)
        return env

    def memory(self, host, files=None):
        path = self.tmp / host / "memory"
        path.mkdir(parents=True, exist_ok=True)
        for name, text in (files or {}).items():
            (path / name).write_text(text)
        return path

    def run(self, host, *argv, run=proc.sh, call=None, env=None):
        """main() for `host`. Returns (status, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        status = sync.main(list(argv), environ=env or self.env(host),
                           run=run, call=call or no_call, out=out, err=err,
                           project=project())
        return status, out.getvalue(), err.getvalue()

    def remote_head(self):
        return git("--git-dir", str(self.remote), "rev-parse",
                   "refs/heads/main").strip()

    def remote_file(self, name):
        return git("--git-dir", str(self.remote), "show", "main:" + name)

    def git_dir(self, host):
        return self.env(host)["FORGE_MEMORY_GIT_DIR"]

    def local_head(self, host):
        return git("--git-dir", self.git_dir(host), "rev-parse",
                   "HEAD").strip()

    def filed(self, host):
        return os.path.join(self.env(host)["FORGE_STATE_DIR"], "widget",
                            "memory-sync", "filed.json")

    def two_hosts(self):
        """one seeded from its memories, then two seeded over nothing but
        an index, so both hold the same tree."""
        self.memory("one", {"MEMORY.md": "- [A](a.md)\n", "a.md": "a\n"})
        self.memory("two", {"MEMORY.md": "- [A](a.md)\n"})
        for host in ("one", "two"):
            status, _, err = self.run(host, "--seed")
            assert status == 0, err


def no_call(*a, **k):
    raise AssertionError("GitHub was called without --file")


def failing(verb, rc=128, said="remote: Write access to repository not "
            "granted."):
    """A run whose git `verb` fails, as a refusal does."""
    def run(args, **kw):
        if verb in args:
            return subprocess.CompletedProcess(args, rc, "", said)
        return proc.sh(args, **kw)
    return run


class FleetCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fleet = Fleet(tmp.name)


class Configuration(unittest.TestCase):

    def test_without_one_it_could_not_ask(self):
        err = io.StringIO()
        status = sync.main([], environ={"HOME": "/nowhere"},
                           project=project(), out=io.StringIO(), err=err,
                           run=no_call, call=no_call)
        self.assertEqual(status, 2)
        self.assertIn("no memory dir or remote", err.getvalue())

    def test_an_ssh_key_is_used_for_this_repository_alone(self):
        g = sync.Git({"git_dir": "/g", "dir": "/d", "host": "h",
                      "ssh_key": "/home/a b/.ssh/k"})
        argv = g.argv("fetch")
        self.assertIn("core.sshCommand=ssh -i '/home/a b/.ssh/k' "
                      "-o IdentitiesOnly=yes", argv)
        self.assertEqual(argv[-1], "fetch")

    def test_without_one_git_uses_the_hosts_own(self):
        g = sync.Git({"git_dir": "/g", "dir": "/d", "host": "h",
                      "ssh_key": None})
        self.assertFalse([a for a in g.argv("fetch") if "sshCommand" in a])

    def test_git_is_bounded_and_runs_in_the_memory_directory(self):
        seen = []

        def run(args, cwd=None, env=None, timeout=None):
            seen.append((cwd, timeout, env.get("X")))
            return subprocess.CompletedProcess(args, 0, "", "")
        sync.Git({"git_dir": "/g", "dir": "/d", "host": "h"}, run,
                 {"X": "y"})("status")
        self.assertEqual(seen, [("/d", sync.GIT_TIMEOUT_S, "y")])


class UnionLines(unittest.TestCase):
    def test_the_repositorys_lines_then_this_hosts_new_ones_once(self):
        self.assertEqual(sync.union_lines("- a\n- b\n", "- b\n- c\n- c\n"),
                         "- a\n- b\n- c\n")

    def test_a_missing_final_newline_is_supplied(self):
        self.assertEqual(sync.union_lines("- a", "- c"), "- a\n- c\n")

    def test_either_side_empty(self):
        self.assertEqual(sync.union_lines("", "- c\n"), "- c\n")
        self.assertEqual(sync.union_lines("- a\n", ""), "- a\n")


class Seed(FleetCase):
    def test_the_first_host_seeds_an_empty_repository(self):
        mem = self.fleet.memory("one", {"MEMORY.md": "- x\n", "x.md": "x\n"})
        status, out, _ = self.fleet.run("one", "--seed")
        self.assertEqual(status, 0)
        self.assertIn("seeded an empty repository from one", out)
        self.assertEqual(self.fleet.remote_file("x.md"), "x\n")
        self.assertEqual(self.fleet.remote_file(".gitattributes"),
                         "MEMORY.md merge=union\n")
        self.assertFalse((mem / ".git").exists(),
                         "the git directory is outside the memories")
        gd = self.fleet.git_dir("one")
        self.assertEqual(git("--git-dir", gd, "config", "core.worktree")
                         .strip(), str(mem))
        self.assertEqual(git("--git-dir", gd, "config", "core.bare").strip(),
                         "false")

    def test_a_second_host_merges_what_it_already_has(self):
        self.fleet.memory("one", {
            "MEMORY.md": "- [A](a.md)\n- [S](same.md)\n- [C](clash.md)\n",
            "a.md": "a\n", "same.md": "same\n", "clash.md": "one's\n"})
        self.assertEqual(self.fleet.run("one", "--seed")[0], 0)
        mem = self.fleet.memory("two", {
            "MEMORY.md": "- [S](same.md)\n- [D](demo.md)\n",
            "same.md": "same\n", "clash.md": "two's\n",
            "demo.md": "demo\n"})
        status, _, err = self.fleet.run("two", "--seed")
        self.assertEqual(status, 1, "a file set aside needs a person")
        self.assertRegex(err, r"set aside in \S+: clash\.md\n")
        seed_dir = Path(self.fleet.env("two")["FORGE_MEMORY_SEED_DIR"])
        # An identical file is left where it is, not set aside.
        self.assertEqual(os.listdir(seed_dir), ["clash.md"])
        # The repository's copy is checked out, and this host's is kept.
        self.assertEqual((mem / "clash.md").read_text(), "one's\n")
        self.assertEqual((seed_dir / "clash.md").read_text(), "two's\n")
        # A file this host lacked is checked out, not deleted remotely.
        self.assertEqual((mem / "a.md").read_text(), "a\n")
        self.assertEqual(self.fleet.remote_file("a.md"), "a\n")
        # This host's own file and index lines reach the repository.
        self.assertEqual(self.fleet.remote_file("demo.md"), "demo\n")
        self.assertEqual(
            self.fleet.remote_file("MEMORY.md"),
            "- [A](a.md)\n- [S](same.md)\n- [C](clash.md)\n- [D](demo.md)\n")
        self.assertEqual((mem / "MEMORY.md").read_text(),
                         self.fleet.remote_file("MEMORY.md"))

    def test_a_host_with_nothing_new_seeds_without_pushing(self):
        self.fleet.memory("one", {"MEMORY.md": "- [A](a.md)\n",
                                  "a.md": "a\n"})
        self.fleet.run("one", "--seed")
        self.fleet.memory("two", {"MEMORY.md": "- [A](a.md)\n"})
        seen = []

        def run(args, **kw):
            seen.append(args)
            return proc.sh(args, **kw)
        status, _, err = self.fleet.run("two", "--seed", run=run)
        self.assertEqual(status, 0, err)
        self.assertEqual([a for a in seen if "push" in a], [])
        self.assertEqual(self.fleet.local_head("two"),
                         self.fleet.remote_head())

    def test_a_seeded_host_is_not_seeded_again(self):
        self.fleet.two_hosts()
        head = self.fleet.local_head("one")
        status, _, err = self.fleet.run("one", "--seed")
        self.assertEqual(status, 2)
        self.assertIn("already exists: this host is seeded", err)
        self.assertEqual(self.fleet.local_head("one"), head)

    def test_no_memory_directory_touches_nothing(self):
        status, _, err = self.fleet.run("one", "--seed")
        self.assertEqual(status, 2)
        self.assertIn("no MEMORY.md", err)
        self.assertFalse(os.path.exists(self.fleet.git_dir("one")))

    def test_a_directory_without_an_index_touches_nothing(self):
        self.fleet.memory("one", {"x.md": "x\n"})
        self.assertEqual(self.fleet.run("one", "--seed")[0], 2)
        self.assertFalse(os.path.exists(self.fleet.git_dir("one")))

    def test_a_seed_that_loses_the_push_race_says_so(self):
        self.fleet.memory("one", {"MEMORY.md": "- a\n"})
        self.fleet.run("one", "--seed")
        self.fleet.memory("two", {"MEMORY.md": "- b\n"})
        run = failing("push", 1, " ! [rejected] HEAD -> main (fetch first)")
        status, _, err = self.fleet.run("two", "--seed", run=run)
        self.assertEqual(status, 1)
        self.assertIn("the next sync rebases and pushes", err)

    def test_a_first_seed_refused_for_access_can_be_run_again(self):
        mem = self.fleet.memory("one", {"MEMORY.md": "- x\n", "x.md": "x\n"})
        status, _, err = self.fleet.run("one", "--seed", run=failing("push"))
        self.assertEqual(status, 2)
        self.assertIn("Write access to repository not granted", err)
        # The host is as it was before the seed.
        self.assertFalse(os.path.exists(self.fleet.git_dir("one")))
        self.assertEqual(sorted(os.listdir(mem)), ["MEMORY.md", "x.md"])
        status, out, err = self.fleet.run("one", "--seed")
        self.assertEqual(status, 0, err)
        self.assertIn("seeded an empty repository from one", out)
        self.assertEqual(self.fleet.remote_file("x.md"), "x\n")

    def test_a_failed_seed_restores_a_gitattributes_it_found(self):
        mem = self.fleet.memory("one", {"MEMORY.md": "- x\n",
                                        ".gitattributes": "*.md text\n"})
        self.assertEqual(self.fleet.run("one", "--seed",
                                        run=failing("push"))[0], 2)
        self.assertEqual((mem / ".gitattributes").read_text(),
                         "*.md text\n")

    def test_a_later_host_whose_fetch_fails_can_be_run_again(self):
        self.fleet.memory("one", {"MEMORY.md": "- a\n"})
        self.assertEqual(self.fleet.run("one", "--seed")[0], 0)
        mem = self.fleet.memory("two", {"MEMORY.md": "- b\n"})
        self.assertEqual(self.fleet.run("two", "--seed",
                                        run=failing("fetch"))[0], 2)
        self.assertFalse(os.path.exists(self.fleet.git_dir("two")))
        self.assertEqual(os.listdir(mem), ["MEMORY.md"])
        self.assertEqual(self.fleet.run("two", "--seed")[0], 0)
        self.assertEqual(self.fleet.remote_file("MEMORY.md"), "- a\n- b\n")

    def test_a_later_host_refused_after_merging_is_left_for_the_sync(self):
        self.fleet.memory("one", {"MEMORY.md": "- a\n"})
        self.assertEqual(self.fleet.run("one", "--seed")[0], 0)
        self.fleet.memory("two", {"MEMORY.md": "- b\n"})
        self.assertEqual(self.fleet.run("two", "--seed",
                                        run=failing("push"))[0], 2)
        # The tree is merged and committed, so nothing is undone; the
        # next sync pushes it.
        self.assertTrue(os.path.isdir(self.fleet.git_dir("two")))
        status, _, err = self.fleet.run("two")
        self.assertEqual(status, 0, err)
        self.assertEqual(self.fleet.remote_file("MEMORY.md"), "- a\n- b\n")


class Sync(FleetCase):
    def test_a_memory_written_on_one_host_reaches_the_other(self):
        self.fleet.two_hosts()
        self.fleet.memory("one", {"new.md": "learned\n"})
        self.assertEqual(self.fleet.run("one")[0], 0)
        self.assertEqual(self.fleet.run("two")[0], 0)
        self.assertEqual((self.fleet.memory("two") / "new.md").read_text(),
                         "learned\n")

    def test_both_hosts_adding_to_the_index_keep_both_lines(self):
        self.fleet.two_hosts()
        for host in ("one", "two"):
            path = self.fleet.memory(host) / "MEMORY.md"
            path.write_text(path.read_text() + "- from %s\n" % host)
        self.assertEqual(self.fleet.run("one")[0], 0)
        status, out, err = self.fleet.run("two")
        self.assertEqual(status, 0, err)
        self.assertEqual(out.strip(), "forge-memory-sync: pushed")
        self.assertEqual(self.fleet.remote_file("MEMORY.md"),
                         "- [A](a.md)\n- from one\n- from two\n")

    def test_a_topic_file_edited_on_both_stops_and_loses_nothing(self):
        self.fleet.two_hosts()
        (self.fleet.memory("one") / "a.md").write_text("one says\n")
        self.fleet.run("one")
        mem2 = self.fleet.memory("two")
        (mem2 / "a.md").write_text("two says\n")
        remote_before = self.fleet.remote_head()
        status, _, err = self.fleet.run("two")
        self.assertEqual(status, 1)
        self.assertIn("conflict with the other hosts in: a.md", err)
        # The working tree is as it was, and the local commit survives.
        self.assertEqual((mem2 / "a.md").read_text(), "two says\n")
        self.assertEqual(git("--git-dir", self.fleet.git_dir("two"), "show",
                             "HEAD:a.md"), "two says\n")
        self.assertFalse(sync.rebase_in_progress(
            {"git_dir": self.fleet.git_dir("two")}), "the rebase was aborted")
        self.assertEqual(self.fleet.remote_head(), remote_before)
        # And the next run, still conflicted, stops the same way.
        self.assertEqual(self.fleet.run("two")[0], 1)

    def test_nothing_changed_commits_and_pushes_nothing(self):
        self.fleet.two_hosts()
        head, local = self.fleet.remote_head(), self.fleet.local_head("one")
        status, out, _ = self.fleet.run("one")
        self.assertEqual(status, 0)
        self.assertIn("nothing to commit", out)
        self.assertEqual((self.fleet.remote_head(),
                          self.fleet.local_head("one")), (head, local))

    def test_a_deletion_on_one_host_is_a_deletion_on_the_other(self):
        self.fleet.two_hosts()
        (self.fleet.memory("one") / "a.md").unlink()
        self.fleet.run("one")
        self.fleet.run("two")
        self.assertFalse((self.fleet.memory("two") / "a.md").exists())

    def test_a_push_refused_once_is_retried_from_the_rebase(self):
        self.fleet.two_hosts()
        self.fleet.memory("two", {"b.md": "b\n"})
        raced = []

        def run(args, **kw):
            # one pushes between two's rebase and its push, once.
            if "push" in args and not raced:
                raced.append(1)
                self.fleet.memory("one", {"c.md": "c\n"})
                self.fleet.run("one")
            return proc.sh(args, **kw)
        status, out, err = self.fleet.run("two", run=run)
        self.assertEqual(status, 0, err)
        self.assertIn("pushed", out)
        self.assertEqual(self.fleet.remote_file("b.md"), "b\n")
        self.assertEqual(self.fleet.remote_file("c.md"), "c\n")

    def test_a_push_refused_twice_waits_for_the_next_run(self):
        self.fleet.two_hosts()
        self.fleet.memory("two", {"b.md": "b\n"})
        pushes = []

        def run(args, **kw):
            if "push" in args:
                pushes.append(1)
                return subprocess.CompletedProcess(
                    args, 1, "", " ! [rejected] HEAD -> main "
                    "(non-fast-forward)")
            return proc.sh(args, **kw)
        status, _, err = self.fleet.run("two", run=run)
        self.assertEqual((status, len(pushes)), (1, 2))
        self.assertIn("push refused twice", err)

    def test_a_push_refused_for_access_is_not_retried(self):
        self.fleet.two_hosts()
        self.fleet.memory("two", {"b.md": "b\n"})
        pushes = []

        def run(args, **kw):
            if "push" in args:
                pushes.append(1)
                return subprocess.CompletedProcess(
                    args, 128, "", "remote: Permission to x denied")
            return proc.sh(args, **kw)
        status, _, err = self.fleet.run("two", run=run)
        self.assertEqual((status, len(pushes)), (2, 1))
        self.assertIn("push failed: remote: Permission", err)

    def test_a_git_that_times_out_could_not_ask(self):
        self.fleet.two_hosts()
        status, _, err = self.fleet.run(
            "one", run=failing("fetch", 124, "timed out after 120s"))
        self.assertEqual(status, 2)
        self.assertIn("git fetch failed: timed out", err)

    def test_an_unseeded_host_is_2(self):
        self.fleet.memory("one", {"MEMORY.md": "- x\n"})
        status, _, err = self.fleet.run("one")
        self.assertEqual(status, 2)
        self.assertIn("run forge-memory-sync --seed", err)

    def test_no_memory_directory_is_2(self):
        self.assertEqual(self.fleet.run("one")[0], 2)

    def test_a_rebase_in_progress_is_left_alone(self):
        self.fleet.two_hosts()
        (Path(self.fleet.git_dir("one")) / "rebase-merge").mkdir()
        self.fleet.memory("one", {"half.md": "half-resolved\n"})
        local = self.fleet.local_head("one")
        status, _, err = self.fleet.run("one")
        self.assertEqual(status, 1)
        self.assertIn("a rebase is in progress", err)
        self.assertEqual(self.fleet.local_head("one"), local,
                         "nothing committed into somebody's resolution")

    def test_rebase_apply_counts_as_in_progress_too(self):
        with tempfile.TemporaryDirectory() as gd:
            cfg = {"git_dir": gd}
            self.assertFalse(sync.rebase_in_progress(cfg))
            os.mkdir(os.path.join(gd, "rebase-apply"))
            self.assertTrue(sync.rebase_in_progress(cfg))

    def test_a_git_failure_is_2(self):
        self.fleet.two_hosts()
        status, _, err = self.fleet.run(
            "one", run=failing("fetch", 128, "could not resolve host"))
        self.assertEqual(status, 2)
        self.assertIn("git fetch failed: could not resolve host", err)

    def test_another_branch_from_the_configuration(self):
        git("--git-dir", str(self.fleet.remote), "symbolic-ref", "HEAD",
            "refs/heads/trunk")
        self.fleet.memory("one", {"MEMORY.md": "- x\n"})
        out, err = io.StringIO(), io.StringIO()
        status = sync.main(["--seed"], environ=self.fleet.env("one"),
                           project=project(dir="/unused", remote="/unused",
                                           branch="trunk"),
                           out=out, err=err, call=no_call)
        self.assertEqual(status, 0, err.getvalue())
        self.assertEqual(git("--git-dir", str(self.fleet.remote), "show",
                             "trunk:MEMORY.md"), "- x\n")


class TheToken(FleetCase):
    SENTINEL = "ghs_sentinel_token_value"

    def test_it_is_in_no_argument_list_and_no_output(self):
        self.fleet.two_hosts()
        self.fleet.memory("one", {"n.md": "n\n"})
        seen = []

        def run(args, **kw):
            seen.append(args)
            return proc.sh(args, **kw)
        status, out, err = self.fleet.run(
            "one", run=run, env=self.fleet.env("one", GH_TOKEN=self.SENTINEL))
        self.assertEqual(status, 0)
        self.assertTrue(seen)
        for args in seen:
            self.assertNotIn(self.SENTINEL, " ".join(args))
        self.assertNotIn(self.SENTINEL, out + err)

    def test_the_helper_answers_with_the_token_from_the_environment(self):
        r = subprocess.run(
            ["git", "-c", "credential.helper=", "-c",
             "credential.helper=" + sync.HELPER, "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n", capture_output=True,
            text=True, env=dict(os.environ, GH_TOKEN=self.SENTINEL,
                                **ISOLATED))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("username=x-access-token\n", r.stdout)
        self.assertIn("password=%s\n" % self.SENTINEL, r.stdout)

    def test_every_git_call_clears_other_helpers_first(self):
        argv = sync.Git({"git_dir": "/g", "dir": "/", "host": "h"}) \
            .argv("status")
        i = argv.index("credential.helper=")
        self.assertEqual(argv[i + 2], "credential.helper=" + sync.HELPER)


class FakeGitHub:
    """GitHub's issues API, recorded. `open` is the open issue's number."""

    def __init__(self, open_number=None, fail_list=False):
        self.calls, self.open, self.fail_list = [], open_number, fail_list

    def __call__(self, url, token, scheme="Bearer", method="GET", body=None):
        self.calls.append((method, url, body, token))
        if method == "GET":
            if self.fail_list:
                return 502, {}
            items = [{"number": 7, "title": "something else"},
                     {"number": 8, "title": "forge-memory-sync: two has "
                      "stopped syncing", "pull_request": {}}]
            if self.open is not None:
                items.append({"number": self.open,
                              "title": "forge-memory-sync: two has stopped "
                              "syncing"})
            return 200, items
        if url.endswith("/issues"):
            self.open = 900
            return 201, {"number": 900, "html_url":
                         "https://github.com/example/widget/issues/900"}
        return 201, {}

    def kinds(self):
        return [("create" if u.endswith("/issues") else "comment")
                for m, u, _b, _t in self.calls if m == "POST"]


class File(FleetCase):
    def conflicted(self):
        self.fleet.two_hosts()
        (self.fleet.memory("one") / "a.md").write_text("one\n")
        self.fleet.run("one")
        (self.fleet.memory("two") / "a.md").write_text("two\n")

    def run_two(self, gh, *argv):
        return self.fleet.run("two", "--file", *argv, call=gh,
                              env=self.fleet.env("two", GH_TOKEN="tok"))

    def test_a_conflict_is_filed_once_with_the_host(self):
        self.conflicted()
        gh = FakeGitHub()
        status, out, _ = self.run_two(gh)
        self.assertEqual(status, 1)
        self.assertIn("filed https://github.com/example/widget/issues/900",
                      out)
        method, url, body, token = [c for c in gh.calls
                                    if c[0] == "POST"][0]
        self.assertTrue(url.endswith("/repos/example/widget/issues"))
        self.assertEqual(token, "tok")
        self.assertEqual(body["title"],
                         "forge-memory-sync: two has stopped syncing")
        self.assertEqual(body["labels"], [])
        self.assertIn("conflict with the other hosts in: a.md", body["body"])
        self.assertTrue(body["body"].rstrip().endswith("— forge-memory-sync"))
        # The same stop again says so, and writes nothing.
        status, out, _ = self.run_two(gh)
        self.assertIn("already reported on #900", out)
        self.assertEqual(gh.kinds(), ["create"])

    def test_the_configurations_labels_are_used_and_listed_by(self):
        self.conflicted()
        gh = FakeGitHub()
        out, err = io.StringIO(), io.StringIO()
        sync.main(["--file"], environ=self.fleet.env("two", GH_TOKEN="t"),
                  call=gh, out=out, err=err,
                  project=project(dir="/u", remote="/u", labels=["bug"]))
        listing = [u for m, u, _b, _t in gh.calls if m == "GET"][0]
        self.assertIn("labels=bug", listing)
        self.assertEqual([b for m, _u, b, _t in gh.calls
                          if m == "POST"][0]["labels"], ["bug"])

    def test_a_different_stop_is_a_comment_on_the_same_issue(self):
        self.conflicted()
        gh = FakeGitHub()
        self.run_two(gh)
        (Path(self.fleet.git_dir("two")) / "rebase-merge").mkdir()
        status, out, _ = self.run_two(gh)
        self.assertIn("commented on #900", out)
        self.assertEqual(gh.kinds(), ["create", "comment"])

    def test_an_all_clear_forgets_so_the_next_stop_is_reported(self):
        self.conflicted()
        gh = FakeGitHub()
        self.run_two(gh)
        filed = self.fleet.filed("two")
        self.assertTrue(os.path.exists(filed))
        with open(filed) as fh:
            self.assertIn("a.md", json.load(fh))
        (self.fleet.memory("two") / "a.md").write_text("one\n")
        git("--git-dir", self.fleet.git_dir("two"), "reset", "-q", "--hard",
            "origin/main")
        status, _, err = self.run_two(gh)
        self.assertEqual(status, 0, err)
        self.assertFalse(os.path.exists(filed))
        self.assertEqual(gh.kinds(), ["create"], "closes nothing")

    def test_a_failed_listing_files_nothing_and_says_so(self):
        self.conflicted()
        gh = FakeGitHub(fail_list=True)
        status, _, err = self.run_two(gh)
        self.assertEqual(status, 1)
        self.assertIn("could not report on GitHub", err)
        self.assertEqual(gh.kinds(), [])

    def test_a_run_that_could_not_ask_is_not_filed(self):
        gh = FakeGitHub()
        self.assertEqual(self.fleet.run("one", "--file", call=gh)[0], 2)
        self.assertEqual(gh.calls, [])

    def test_a_seed_that_set_files_aside_is_filed(self):
        self.fleet.memory("one", {"MEMORY.md": "- x\n", "c.md": "1\n"})
        self.fleet.run("one", "--seed")
        self.fleet.memory("two", {"MEMORY.md": "- x\n", "c.md": "2\n"})
        gh = FakeGitHub()
        status, _, _ = self.run_two(gh, "--seed")
        self.assertEqual(status, 1)
        body = [b for m, _u, b, _t in gh.calls if m == "POST"][0]["body"]
        self.assertIn("set aside in", body)
        self.assertIn("c.md", body)


class Run(unittest.TestCase):
    def test_an_unexpected_error_is_2_with_its_traceback(self):
        real = sync.main
        sync.main = lambda argv=None: 1 / 0
        err = io.StringIO()
        import contextlib
        try:
            with contextlib.redirect_stderr(err):
                self.assertEqual(sync.run([]), 2)
        finally:
            sync.main = real
        self.assertIn("ZeroDivisionError", err.getvalue())
        self.assertIn("exiting 2", err.getvalue())

    def test_a_usage_error_stays_argparses(self):
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as got:
                sync.run(["--bogus"])
        self.assertEqual(got.exception.code, 2)

    def test_the_script_runs_as_a_program(self):
        r = subprocess.run([str(ROOT / "bin" / "forge-memory-sync"),
                            "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--seed", r.stdout)
        self.assertIn("--file", r.stdout)


if __name__ == "__main__":
    unittest.main()
