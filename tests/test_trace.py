"""Tests for agent_forge/trace.py and the tracer library it builds
(docs/designs/0005).

`Library` compiles lib/forge-trace.c with the host's C compiler and
runs real programs under it: whether every process a step starts keeps
the tracer, an `env -i` or a cleared environment included, is only
shown by doing it. It is skipped where there is no compiler.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from agent_forge import trace
from tests.helpers import done

T0 = datetime.datetime(2026, 10, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)


def parse(stamp):
    return datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class Build(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_a_failed_compile_gives_none_and_leaves_nothing(self):
        lib = trace.build(self.dir, lambda args, **_: done(args, 1, "", "no"))
        self.assertIsNone(lib)
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_missing_source_gives_none(self):
        self.assertIsNone(trace.build(self.dir, source="/no/such.c"))

    def test_built_once_per_source(self):
        calls = []

        def run(args, **_):
            calls.append(args)
            with open(args[args.index("-o") + 1], "w") as handle:
                handle.write("lib")
            return done(args)
        first = trace.build(self.dir, run)
        self.assertEqual(trace.build(self.dir, run), first)
        self.assertEqual(len(calls), 1)
        self.assertRegex(os.path.basename(first),
                         r"^forge-trace-[0-9a-f]{16}\.so$")


@unittest.skipUnless(shutil.which("cc"), "no C compiler")
class Library(unittest.TestCase):
    """The real library, under real programs."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lib = trace.build(os.path.join(cls.tmp.name, "lib"))
        if cls.lib is None:
            raise unittest.SkipTest("the tracer would not build here")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = os.path.realpath(tmp.name)
        for name in "ABCDEFGH":
            with open(os.path.join(self.wt, name), "w") as handle:
                handle.write(name)
        os.mkdir(os.path.join(self.wt, "docs"))
        self.record = os.path.join(self.wt, ".record")

    def traced(self, script):
        env = trace.preload_env(dict(os.environ), self.lib, self.record)
        got = subprocess.run(["sh", "-c", script], cwd=self.wt, env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(got.returncode, 0, got.stderr)
        entries = trace.read_preload(self.record)
        return entries, trace.relative(entries, self.wt, [".record"])

    def test_every_process_keeps_the_tracer(self):
        # A fork and exec as such, `env -i` twice over, Python's
        # subprocess with an empty environment, posix_spawn with one,
        # and system() from a process that cleared its own.
        py = textwrap.dedent("""\
            import os, subprocess
            subprocess.run(["cat", "C"], env={"PATH": os.environ["PATH"]},
                           stdout=subprocess.DEVNULL)
            subprocess.run(["/bin/cat", "D"], env={},
                           stdout=subprocess.DEVNULL)
            pid = os.posix_spawn("/bin/cat", ["cat", "E"], {})
            os.waitpid(pid, 0)
            os.environ.clear()
            os.system("/bin/cat F >/dev/null")
            """)
        with open(os.path.join(self.wt, "child.py"), "w") as handle:
            handle.write(py)
        script = ("cat A >/dev/null; "
                  "env -i sh -c 'cat B >/dev/null; env -i /bin/cat G' "
                  ">/dev/null; "
                  "%s child.py >/dev/null; "
                  "(cat H) >/dev/null" % sys.executable)
        _, paths = self.traced(script)
        for name in "ABCDEFGH":
            self.assertIn(name, paths)
        self.assertIn("child.py", paths)

    def test_a_path_looked_for_and_missing_is_recorded(self):
        _, paths = self.traced("test -e MISSING || true; "
                               "cat NOPE 2>/dev/null || true")
        self.assertIn("MISSING", paths)
        self.assertIn("NOPE", paths)

    def test_a_listed_directory_is_recorded(self):
        _, paths = self.traced("ls docs >/dev/null")
        self.assertIn("docs", paths)

    def test_relative_to_a_directory_descriptor(self):
        py = ("import os; d = os.open('docs', os.O_RDONLY); "
              "os.close(os.open('../A', os.O_RDONLY, dir_fd=d))")
        _, paths = self.traced("%s -c \"%s\"" % (sys.executable, py))
        self.assertIn("A", paths)

    def test_executed_programs_are_recorded_as_such(self):
        entries, _ = self.traced("cat A >/dev/null")
        executed = {os.path.basename(p) for kind, p in entries if kind == "x"}
        self.assertIn("cat", executed)
        self.assertTrue(trace.plausible(entries, "sh"))

    def test_a_closed_record_descriptor_is_reopened_not_reused(self):
        # A program that closes every descriptor and opens another file
        # in the record's old slot must not find trace lines written
        # into it.
        py = textwrap.dedent("""\
            import os
            os.closerange(3, 1024)
            fd = os.open("out", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            os.stat("A")
            os.close(fd)
            print(open("out").read() == "")
            """)
        with open(os.path.join(self.wt, "closer.py"), "w") as handle:
            handle.write(py)
        env = trace.preload_env(dict(os.environ), self.lib, self.record)
        got = subprocess.run([sys.executable, "closer.py"], cwd=self.wt,
                             env=env, capture_output=True, text=True)
        self.assertEqual(got.stdout.strip(), "True")
        self.assertIn("A", trace.relative(trace.read_preload(self.record),
                                          self.wt))

    def test_without_a_record_named_it_does_nothing(self):
        env = dict(os.environ, LD_PRELOAD=self.lib)
        env.pop(trace.TRACE_VAR, None)
        got = subprocess.run(["cat", "A"], cwd=self.wt, env=env,
                             capture_output=True, text=True)
        self.assertEqual(got.stdout, "A")
        self.assertFalse(os.path.exists(self.record))


class Preload(unittest.TestCase):

    def test_the_library_goes_first_and_the_record_is_named(self):
        env = trace.preload_env({"LD_PRELOAD": "/x.so"}, "/t.so", "/rec")
        self.assertEqual(env["LD_PRELOAD"], "/t.so:/x.so")
        self.assertEqual(env[trace.TRACE_VAR], "/rec")
        self.assertEqual(trace.preload_env({}, "/t.so", "/r")["LD_PRELOAD"],
                         "/t.so")

    def test_reading_skips_what_isnt_a_line(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("o /a\nx /bin/cat\ngarbage\no \n")
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(trace.read_preload(handle.name),
                         [("o", "/a"), ("x", "/bin/cat")])
        self.assertEqual(trace.read_preload("/no/such"), [])


class Strace(unittest.TestCase):

    def test_the_lines_strace_writes(self):
        text = textwrap.dedent("""\
            1234 execve("/usr/bin/make", ["make", "test"], 0x7ff /* 3 vars */) = 0
            1234 openat(AT_FDCWD</wt>, "Makefile", O_RDONLY) = 3</wt/Makefile>
            1235 newfstatat(3</wt/src>, "lib.rs", {st_mode=S_IFREG}, 0) = 0
            1235 access("/wt/missing", R_OK) = -1 ENOENT (No such file)
            1236 openat(AT_FDCWD, "a\\"b", O_RDONLY) = 3
            1236 +++ exited with 0 +++
            """)
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(text)
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(trace.read_strace(handle.name, "/wt"), [
            ("x", "/usr/bin/make"), ("o", "/wt/Makefile"),
            ("o", "/wt/src/lib.rs"), ("o", "/wt/missing"),
            ("o", '/wt/a"b')])

    def test_the_prefix(self):
        prefix = trace.strace_prefix("/rec")
        self.assertEqual(prefix[0], "strace")
        self.assertIn("-f", prefix)
        self.assertEqual(prefix[prefix.index("-o") + 1], "/rec")
        self.assertEqual(prefix[-1], "--")


class Paths(unittest.TestCase):

    def test_inside_the_worktree_less_git_and_caches(self):
        entries = [("o", "/wt/src/a.rs"), ("o", "/wt/./b"), ("o", "/wt"),
                   ("o", "/wtx/c"), ("o", "/etc/passwd"),
                   ("o", "/wt/.git/HEAD"), ("o", "/wt/target/x"),
                   ("x", "/wt/tools/run")]
        self.assertEqual(trace.relative(entries, "/wt", ["target"]),
                         [".", "b", "src/a.rs", "tools/run"])

    def test_a_worktree_reached_through_a_link(self):
        with tempfile.TemporaryDirectory() as real:
            link = real + "-link"
            os.symlink(real, link)
            self.addCleanup(os.unlink, link)
            self.assertEqual(trace.relative(
                [("o", os.path.join(os.path.realpath(real), "f"))], link),
                ["f"])

    def test_directories_are_parents_and_those_listed(self):
        with tempfile.TemporaryDirectory() as wt:
            os.makedirs(os.path.join(wt, "docs", "designs"))
            self.assertEqual(
                trace.directories(["Makefile", "src/a.rs", "docs/designs"],
                                  wt),
                [".", "docs", "docs/designs", "src"])

    def test_plausible(self):
        self.assertTrue(trace.plausible([("x", "/usr/bin/make")], "make"))
        self.assertTrue(trace.plausible([("o", "/usr/bin/npm")], "npm"))
        self.assertFalse(trace.plausible([], "make"))
        self.assertFalse(trace.plausible([("o", "/wt/Makefile")], "make"))
        if shutil.which("sh") and os.path.basename(
                os.path.realpath(shutil.which("sh"))) != "sh":
            real = os.path.basename(os.path.realpath(shutil.which("sh")))
            self.assertTrue(trace.plausible([("x", "/usr/bin/" + real)],
                                            "sh"))


class Records(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name

    def write(self, tree, at):
        return trace.write_record(self.state, tree, "c" + tree, "digest",
                                  iso(at), "preload", {"test": ["a"]},
                                  {"test": ["."]})

    def test_written_and_loaded_newest_first(self):
        self.write("t1", T0)
        path = self.write("t2", T0 + datetime.timedelta(hours=1))
        loaded = trace.load_records(self.state)
        self.assertEqual([r["tree"] for _, r in loaded], ["t2", "t1"])
        self.assertEqual(loaded[0][0], path)
        with open(path) as handle:
            self.assertEqual(json.load(handle)["commit"], "ct2")

    def test_unreadable_and_temporary_files_are_left_out(self):
        self.write("t1", T0)
        folder = trace.records_dir(self.state)
        for name, text in ((".trace-1-0", "o /x"), ("bad.json", "{"),
                           ("odd.json", "[]")):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write(text)
        self.assertEqual([r["tree"] for _, r in
                          trace.load_records(self.state)], ["t1"])
        self.assertEqual(trace.load_records("/no/such"), [])

    def test_old_ones_go_but_the_newest_stays(self):
        self.write("old", T0 - datetime.timedelta(days=30))
        self.write("older", T0 - datetime.timedelta(days=31))
        trace.prune_records(self.state, T0, parse)
        self.assertEqual([r["tree"] for _, r in
                          trace.load_records(self.state)], ["old"])
        self.write("new", T0)
        trace.prune_records(self.state, T0, parse)
        self.assertEqual([r["tree"] for _, r in
                          trace.load_records(self.state)], ["new"])


class Selection(unittest.TestCase):

    RECORD = {"reads": {"docs": ["README.md", "designs/0001.md", "Makefile"],
                        "rust": ["Cargo.toml", "src/lib.rs", "designs/0001.md",
                                 "fixtures"],
                        "web": ["web/package.json", "web/src/app.ts"]},
              "dirs": {"docs": [".", "designs"],
                       "rust": [".", "src", "fixtures"],
                       "web": ["web", "web/src"]}}
    NAMES = ["docs", "rust", "web", "fmt"]

    def select(self, diff, always=()):
        changed, touched = trace.changes(diff)
        return trace.select(self.NAMES, set(always), self.RECORD, changed,
                            touched)

    def test_changes(self):
        self.assertEqual(trace.changes("M\ta\nA\tb/c\nD\td\nbad\n"),
                         ({"a", "b/c", "d"}, {"b/c", "d"}))

    def test_a_step_runs_for_what_it_read(self):
        ran, skipped = self.select("M\tweb/src/app.ts\n")
        self.assertEqual(ran, {"web": "web/src/app.ts",
                               "fmt": "not in the record"})
        self.assertEqual(skipped, ["docs", "rust"])

    def test_a_design_read_by_the_tests_runs_them(self):
        # The coupling the record makes visible.
        ran, _ = self.select("M\tdesigns/0001.md\n", always=["fmt"])
        self.assertEqual(ran, {"docs": "designs/0001.md",
                               "rust": "designs/0001.md", "fmt": "always"})

    def test_a_new_file_in_a_directory_read_from(self):
        ran, skipped = self.select("A\tsrc/new.rs\n", always=["fmt"])
        self.assertEqual(ran, {"rust": "src/new.rs (in src)",
                               "fmt": "always"})
        self.assertEqual(skipped, ["docs", "web"])

    def test_a_deleted_file_at_the_top(self):
        ran, _ = self.select("D\tNOTES\n", always=["fmt"])
        self.assertEqual(ran["docs"], "NOTES (in the top)")

    def test_a_path_under_a_changed_directory(self):
        ran, _ = self.select("M\tfixtures\n", always=["fmt"])
        self.assertEqual(ran["rust"], "fixtures")

    def test_nothing_reads_it(self):
        # Changed, not added: what the web step lists is the same.
        ran, skipped = self.select("M\tweb/README\n", always=["fmt"])
        self.assertEqual(ran, {"fmt": "always"})
        self.assertEqual(skipped, ["docs", "rust", "web"])


if __name__ == "__main__":
    unittest.main()
