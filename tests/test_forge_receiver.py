"""Tests for bin/forge-receiver.

The HTTP tests run the real server on an ephemeral loopback port and
send it raw requests, so the checks happen in the order a delivery
meets them. No tool is ever started: `route` gets a fake `run`, and a
status event's lookup a fake GitHub. No test mints a token or reaches
GitHub.
"""

import hashlib
import hmac
import http.client
import json
import os
import unittest.mock
import socket
import tempfile
import threading
import unittest

from agent_forge import config
from tests.helpers import ROOT, done, example, load_tool

rc = load_tool("forge-receiver")

SECRET = b"0f" * 32
SHA = "5b257c8" + "0" * 33
BUILD = ("forge-pr-check", "--post")
MERGER = ("forge-merger",)


def project(**receiver):
    """The example project, its receiver section changed by `receiver`
    (a None value removes the key)."""
    data = example()
    for key, value in receiver.items():
        if value is None:
            data["receiver"].pop(key, None)
        else:
            data["receiver"][key] = value
    return config.Project(data, None)


def configure(**receiver):
    rc.configure(project(**receiver))


configure()


def sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ServerTest(unittest.TestCase):
    def setUp(self):
        configure()
        self.addCleanup(configure)
        self.lines = []
        self.debouncer = rc.Debouncer(wait_s=20, clock=Clock())
        self.server = rc.make_server(0, SECRET, self.lines.append,
                                     self.debouncer, host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, body=b"{}", event="issues", delivery="d-1",
                signature=None, method="POST", path="/hook", headers=None,
                length=True):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=5)
        self.addCleanup(conn.close)
        conn.putrequest(method, path, skip_accept_encoding=True)
        sent = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": (sign(body) if signature is None
                                        else signature)}
        if length:
            sent["Content-Length"] = str(len(body))
        sent.update(headers or {})
        for key, value in sent.items():
            if value is not None:
                conn.putheader(key, value)
        conn.endheaders()
        if length and method == "POST":
            conn.send(body)
        got = conn.getresponse()
        return got.status, got.read().decode().strip()

    def pending(self):
        return sorted(self.debouncer.pending)

    def post(self, event, payload, **kw):
        return self.request(json.dumps(payload).encode(), event, **kw)

    def test_an_issue_comment_is_accepted_and_queued(self):
        got = self.post("issue_comment", {"issue": {"number": 37}})
        self.assertEqual(got, (202, "accepted"))
        self.assertEqual(self.pending(), [("item", 37)])

    def test_each_item_event_names_its_item(self):
        for i, (event, field) in enumerate(sorted(rc.ITEM_OF.items())):
            self.post(event, {field: {"number": 10 + i}},
                      delivery="d-%d" % i)
        self.assertEqual(self.pending(), [("item", 10 + i)
                                          for i in range(4)])

    def test_a_bad_signature_is_refused_and_nothing_queued(self):
        got = self.post("issues", {"issue": {"number": 1}},
                        signature=sign(b"other"))
        self.assertEqual(got[0], 401)
        self.assertEqual(self.pending(), [])

    def test_a_signature_under_another_secret_is_refused(self):
        body = json.dumps({"issue": {"number": 1}}).encode()
        got = self.request(body, signature=sign(body, b"guess"))
        self.assertEqual(got[0], 401)

    def test_sha1_alone_is_refused(self):
        body = json.dumps({"issue": {"number": 1}}).encode()
        sha1 = "sha1=" + hmac.new(SECRET, body, hashlib.sha1).hexdigest()
        got = self.request(body, signature="",
                           headers={"X-Hub-Signature": sha1})
        self.assertEqual(got[0], 401)
        self.assertEqual(self.pending(), [])

    def test_a_non_ascii_signature_is_refused_not_a_crash(self):
        got = self.request(b"{}", signature="sha256=éé")
        self.assertEqual(got[0], 401)

    def test_the_signature_is_checked_before_parsing(self):
        got = self.request(b"not json", signature="sha256=00")
        self.assertEqual(got[0], 401)
        self.assertEqual(self.request(b"not json", delivery="d-2"),
                         (400, "not JSON"))

    def test_no_length_is_411(self):
        got = self.request(b"", length=False)
        self.assertEqual(got[0], 411)

    def test_over_the_cap_is_413_before_reading(self):
        got = self.request(b"", headers={
            "Content-Length": str(rc.MAX_BODY + 1)}, length=False)
        self.assertEqual(got[0], 413)

    def test_the_cap_is_configured(self):
        configure(max_body=10)
        body = json.dumps({"issue": {"number": 1}}).encode()
        self.assertEqual(self.request(body)[0], 413)
        self.assertEqual(self.request(b"{}", delivery="d-2"),
                         (202, "ignored"))

    def test_a_length_that_is_not_ascii_digits_is_411(self):
        for length in ["²", "¹", "1e3", "-1", "0x10", ""]:
            got = self.request(b"", headers={"Content-Length": length},
                               length=False)
            self.assertEqual(got[0], 411, repr(length))

    def test_a_length_of_many_digits_is_413_not_a_crash(self):
        for length in ["9" * 5000, "0" * 9, "123456789"]:
            got = self.request(b"", headers={"Content-Length": length},
                               length=False)
            self.assertEqual(got[0], 413, length[:12])

    def test_other_paths_and_methods(self):
        self.assertEqual(self.request(path="/")[0], 404)
        self.assertEqual(self.request(method="GET", length=False)[0], 405)
        self.assertEqual(self.request(method="GET", path="/x",
                                      length=False)[0], 404)

    def test_the_path_is_configured(self):
        configure(path="/github")
        self.assertEqual(self.request(path="/hook")[0], 404)
        self.assertEqual(self.request(path="/github", delivery="d-2"),
                         (202, "ignored"))

    def test_a_duplicate_delivery_is_dropped(self):
        self.post("issues", {"issue": {"number": 5}})
        self.debouncer.pending.clear()
        got = self.post("issues", {"issue": {"number": 5}})
        self.assertEqual(got, (202, "duplicate"))
        self.assertEqual(self.pending(), [])

    def test_ping_and_unknown_events_are_ignored(self):
        self.assertEqual(self.post("ping", {"zen": "x"}),
                         (202, "ignored"))
        self.assertEqual(self.post("push", {"ref": "x"}, delivery="d-2"),
                         (202, "ignored"))
        self.assertEqual(self.pending(), [])

    def test_a_number_that_is_not_an_int_is_ignored(self):
        for i, n in enumerate(["37", 3.0, True, -1, 0, None,
                               "1; rm -rf ~"]):
            self.post("issues", {"issue": {"number": n}},
                      delivery="d-%d" % i)
        self.assertEqual(self.pending(), [])

    def test_a_build_status_queues_its_commit(self):
        self.post("status", {"context": "vm/build-and-test", "sha": SHA})
        self.assertEqual(self.pending(), [("commit", SHA)])

    def test_other_statuses_and_bad_shas_are_ignored(self):
        self.post("status", {"context": "ci", "sha": SHA})
        self.post("status", {"context": "vm/build-and-test",
                             "sha": "--help"}, delivery="d-2")
        self.assertEqual(self.pending(), [])

    def test_a_new_head_ticks_the_build_check(self):
        for i, action in enumerate(sorted(rc.NEW_HEAD_ACTIONS)):
            self.debouncer.pending.clear()
            self.post("pull_request", {"action": action,
                                       "pull_request": {"number": 7}},
                      delivery="d-%d" % i)
            self.assertEqual(self.pending(), [("item", 7),
                                              ("tool", BUILD)])

    def test_other_pull_request_actions_route_only(self):
        self.post("pull_request", {"action": "labeled",
                                   "pull_request": {"number": 7}})
        self.assertEqual(self.pending(), [("item", 7)])

    def test_an_approval_ticks_the_merger(self):
        self.post("pull_request_review", {
            "action": "submitted", "review": {"state": "approved"},
            "pull_request": {"number": 7}})
        self.assertEqual(self.pending(), [("item", 7), ("tool", MERGER)])

    def test_other_reviews_route_only(self):
        self.post("pull_request_review", {
            "action": "submitted", "review": {"state": "changes_requested"},
            "pull_request": {"number": 7}})
        self.assertEqual(self.pending(), [("item", 7)])

    def test_a_green_build_ticks_the_merger(self):
        self.post("status", {"context": "vm/build-and-test", "sha": SHA,
                             "state": "success"})
        self.assertEqual(self.pending(), [("commit", SHA),
                                          ("tool", MERGER)])

    def test_a_pending_build_does_not(self):
        self.post("status", {"context": "vm/build-and-test", "sha": SHA,
                             "state": "pending"})
        self.assertEqual(self.pending(), [("commit", SHA)])

    def test_a_push_to_main_ticks_the_merger(self):
        self.post("push", {"ref": "refs/heads/main"})
        self.assertEqual(self.pending(), [("tool", MERGER)])

    def test_a_push_to_a_branch_is_ignored(self):
        self.assertEqual(self.post("push", {"ref": "refs/heads/feature"}),
                         (202, "ignored"))
        self.assertEqual(self.pending(), [])

    def test_the_secret_never_reaches_the_log(self):
        self.post("issues", {"issue": {"number": 1}},
                  signature="sha256=" + SECRET.decode())
        self.post("issues", {"issue": {"number": 2}}, delivery="d-2")
        self.assertFalse(any(SECRET.decode() in line
                             for line in self.lines))

    def test_each_delivery_is_noted_once(self):
        self.post("status", {"context": "vm/build-and-test", "sha": SHA,
                             "state": "success"})
        self.assertEqual(self.lines, [
            "202 status d-1: commit %s, tool forge-merger" % SHA[:10]])


class KeysTest(unittest.TestCase):
    """What the configuration changes about which keys an event gives."""

    def tearDown(self):
        configure()

    def test_the_default_branch_is_the_project_s(self):
        data = example()
        data["default_branch"] = "trunk"
        rc.configure(config.Project(data, None))
        self.assertEqual(rc.keys_for("push", {"ref": "refs/heads/trunk"}),
                         ([("tool", MERGER)], None))
        self.assertEqual(rc.keys_for("push", {"ref": "refs/heads/main"})[0],
                         [])

    def test_the_status_context_is_the_project_s(self):
        data = example()
        data["checks"]["context"] = "build"
        rc.configure(config.Project(data, None))
        self.assertEqual(rc.keys_for("status", {"context": "build",
                                                "sha": SHA})[0],
                         [("commit", SHA)])
        self.assertEqual(rc.keys_for("status", {
            "context": "vm/build-and-test", "sha": SHA}),
            ([], "not build"))

    def test_without_a_route_nothing_is_routed(self):
        configure(route=None)
        self.assertEqual(rc.keys_for("issues", {"issue": {"number": 3}}),
                         ([], "nothing to route or tick"))
        self.assertEqual(rc.keys_for("status", {
            "context": "vm/build-and-test", "sha": SHA,
            "state": "success"}), ([("tool", MERGER)], None))

    def test_an_event_the_table_leaves_out_ticks_nothing(self):
        configure(ticks={"pull_request": [list(BUILD)]})
        self.assertEqual(rc.keys_for("push", {"ref": "refs/heads/main"}),
                         ([], "nothing ticks on a push"))
        self.assertEqual(rc.keys_for("pull_request_review", {
            "action": "submitted", "review": {"state": "approved"},
            "pull_request": {"number": 7}})[0], [("item", 7)])

    def test_issue_events_tick_on_any_delivery(self):
        configure(ticks={"issue_comment": ["widget-triage"]})
        self.assertEqual(rc.keys_for("issue_comment",
                                     {"issue": {"number": 4}})[0],
                         [("item", 4), ("tool", ("widget-triage",))])

    def test_the_order_is_the_table_s_first_mention(self):
        configure(ticks={"push": ["forge-merger", "b"],
                         "status": ["a", "forge-merger"]})
        self.assertEqual(rc.TOOL_ORDER, [MERGER, ("b",), ("a",)])

    def test_the_defaults(self):
        data = example()
        del data["receiver"]
        rc.configure(config.Project(data, None))
        self.assertEqual((rc.ADDRESS, rc.PORT, rc.PATH, rc.MAX_BODY),
                         ("127.0.0.1", 8789, "/hook", 1 << 20))
        self.assertEqual(rc.SECRET_FILE,
                         "~/.config/agent-forge/widget/receiver-secret")
        self.assertEqual((rc.TICKS, rc.ROUTE), ({}, None))
        self.assertEqual(rc.keys_for("push", {"ref": "refs/heads/main"})[0],
                         [])


class DedupeTest(unittest.TestCase):
    def test_bounded_and_oldest_forgotten(self):
        seen = rc.Seen(keep=3)
        self.assertEqual([seen.first(d) for d in "abcd"], [True] * 4)
        self.assertEqual(len(seen.ids), 3)
        self.assertTrue(seen.first("a"))
        self.assertFalse(seen.first("d"))


class PersistentSeenTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, rc.SEEN_FILE)

    def test_a_restart_remembers_the_ids(self):
        seen = rc.Seen(path=self.path)
        self.assertTrue(seen.first("a"))
        self.assertTrue(seen.first("b"))
        again = rc.Seen(path=self.path)
        self.assertFalse(again.first("a"))
        self.assertTrue(again.first("c"))

    def test_the_bound_holds_across_a_restart(self):
        seen = rc.Seen(keep=2, path=self.path)
        for d in "abc":
            seen.first(d)
        with open(self.path) as handle:
            self.assertEqual(json.load(handle), ["b", "c"])
        self.assertEqual(list(rc.Seen(keep=1, path=self.path).ids), ["c"])

    def test_a_missing_or_corrupt_file_starts_empty(self):
        self.assertEqual(list(rc.Seen(path=self.path).ids), [])
        for junk in ("not json", '{"a": 1}', '["a", 3]'):
            with open(self.path, "w") as handle:
                handle.write(junk)
            seen = rc.Seen(path=self.path)
            self.assertEqual(list(seen.ids), ["a"] if "[" in junk else [])
            self.assertTrue(seen.first("z"))

    def test_the_write_is_atomic_and_leaves_no_temporary_file(self):
        seen = rc.Seen(path=self.path)
        seen.first("a")
        self.assertEqual(os.listdir(os.path.dirname(self.path)),
                         [rc.SEEN_FILE])

    def test_a_forgotten_delivery_is_taken_again(self):
        seen = rc.Seen(path=self.path)
        seen.first("a")
        seen.forget("a")
        seen.forget("never")
        self.assertTrue(rc.Seen(path=self.path).first("a"))

    def test_without_a_path_nothing_is_written(self):
        seen = rc.Seen()
        seen.first("a")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [])


class AcceptFailureTest(unittest.TestCase):
    def setUp(self):
        configure()
        self.addCleanup(configure)

    def test_a_delivery_that_fails_is_forgotten_and_answered_500(self):
        class Broken(rc.Debouncer):
            def add(self, kind, value):
                raise RuntimeError("disk full")
        seen = rc.Seen()
        body = json.dumps({"issue": {"number": 4}}).encode()
        got = rc.accept("issues", "d-9", body, seen, Broken())
        self.assertEqual(got[:2], (500, "failed"))
        self.assertEqual(got[2], "FAILED: RuntimeError: disk full")
        good = rc.Debouncer(clock=Clock())
        self.assertEqual(rc.accept("issues", "d-9", body, seen, good)[:2],
                         (202, "accepted"))

    def test_bad_json_stays_seen(self):
        seen = rc.Seen()
        rc.accept("issues", "d-1", b"nope", seen, rc.Debouncer())
        self.assertFalse(seen.first("d-1"))


class CheckSuiteTest(unittest.TestCase):
    def setUp(self):
        configure(ticks={"check_suite": ["forge-merger"]})
        self.addCleanup(configure)

    def suite(self, **kw):
        data = {"conclusion": "success",
                "pull_requests": [{"number": 8}, {"number": 3}]}
        data.update(kw)
        return {"action": "completed", "check_suite": data}

    def test_a_green_suite_routes_its_prs_and_ticks(self):
        keys, why = rc.keys_for("check_suite", self.suite())
        self.assertEqual(keys, [("item", 3), ("item", 8),
                                ("tool", MERGER)])

    def test_a_red_suite_routes_only(self):
        keys, _ = rc.keys_for("check_suite", self.suite(conclusion="failure"))
        self.assertEqual(keys, [("item", 3), ("item", 8)])

    def test_bad_numbers_are_dropped(self):
        keys, _ = rc.keys_for("check_suite", self.suite(
            conclusion="failure", pull_requests=[
                {"number": True}, {"number": "4"}, {"number": -1}, "x",
                {"number": 6}]))
        self.assertEqual(keys, [("item", 6)])
        keys, why = rc.keys_for("check_suite", self.suite(
            conclusion="failure", pull_requests="x"))
        self.assertEqual((keys, why), ([], "nothing to route or tick"))

    def test_only_a_completed_suite_counts(self):
        payload = self.suite()
        payload["action"] = "requested"
        self.assertEqual(rc.keys_for("check_suite", payload),
                         ([], "a check suite not completed"))
        self.assertEqual(rc.keys_for("check_suite", {"action": "completed"}),
                         ([], "a check suite not completed"))

    def test_without_a_route_only_the_tick(self):
        configure(ticks={"check_suite": ["forge-merger"]}, route=None)
        self.assertEqual(rc.keys_for("check_suite", self.suite())[0],
                         [("tool", MERGER)])

    def test_the_configuration_takes_check_suite(self):
        data = example()
        data["receiver"]["ticks"]["check_suite"] = ["forge-merger"]
        self.assertEqual(config.check(data), [])


class WatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.conf = os.path.join(tmp.name, "config.json")
        with open(self.conf, "w") as handle:
            handle.write("{}")
        for v in ("v1", "v2"):
            os.makedirs(os.path.join(tmp.name, v))
            with open(os.path.join(tmp.name, v, "forge-receiver"),
                      "w") as handle:
                handle.write(v)
        self.link = os.path.join(tmp.name, "current")
        os.symlink("v1", self.link)
        self.me = os.path.join(self.link, "forge-receiver")

    def test_nothing_changed(self):
        self.assertIsNone(rc.Watch([self.me, self.conf]).changed())

    def test_a_new_install_is_a_change(self):
        watch = rc.Watch([self.me, self.conf])
        os.remove(self.link)
        os.symlink("v2", self.link)
        self.assertEqual(watch.changed(), self.me)

    def test_a_rewritten_configuration_is_a_change(self):
        watch = rc.Watch([self.me, self.conf])
        tmp = self.conf + ".new"
        with open(tmp, "w") as handle:
            handle.write('{"x": 1}')
        os.replace(tmp, self.conf)
        self.assertEqual(watch.changed(), self.conf)

    def test_a_vanished_file_is_a_change(self):
        watch = rc.Watch([self.me, self.conf])
        os.remove(self.conf)
        self.assertEqual(watch.changed(), self.conf)

    def test_the_loop_says_so_and_calls_back_once(self):
        lines, calls = [], []
        watch = rc.Watch([self.conf])
        os.remove(self.conf)
        stop = threading.Event()
        rc.watch_loop(watch, stop, lines.append, lambda: calls.append(1),
                      interval=0.01)
        self.assertEqual(calls, [1])
        self.assertTrue(lines[0].startswith("RESTART: %s changed"
                                            % self.conf))

    def test_the_loop_ends_on_stop(self):
        stop = threading.Event()
        stop.set()
        rc.watch_loop(rc.Watch([self.conf]), stop, print,
                      lambda: self.fail("called"), interval=0.01)


class DebounceTest(unittest.TestCase):
    def test_a_burst_makes_one_key_due_after_its_latest_event(self):
        clock = Clock()
        d = rc.Debouncer(wait_s=20, clock=clock)
        d.add("item", 5)
        clock.now += 15
        d.add("item", 5)
        d.add("item", 6)
        clock.now += 5
        self.assertEqual(d.due(), [])  # 5's latest event is 5 s old
        clock.now += 15
        self.assertEqual(d.due(), [("item", 5), ("item", 6)])
        self.assertEqual(d.due(), [])

    def test_a_steady_stream_is_due_at_the_cap(self):
        clock = Clock()
        d = rc.Debouncer(wait_s=20, clock=clock)
        self.assertEqual(d.cap_s, 60)
        for _ in range(5):
            d.add("item", 5)
            self.assertEqual(d.due(), [])
            clock.now += 12
        self.assertEqual(d.due(), [("item", 5)])

    def test_the_cap_follows_the_module_s_default(self):
        self.assertEqual(rc.DEBOUNCE_CAP_S, 3 * rc.DEBOUNCE_S)
        self.assertEqual(rc.Debouncer().cap_s, rc.DEBOUNCE_CAP_S)

    def test_items_and_commits_due_together(self):
        clock = Clock()
        d = rc.Debouncer(wait_s=20, clock=clock)
        d.add("commit", SHA)
        d.add("item", 5)
        clock.now += 20
        self.assertEqual(d.due(), [("commit", SHA), ("item", 5)])


class FakeGitHub:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def api(self, *args):
        self.calls.append(args)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class RouteTest(unittest.TestCase):
    def setUp(self):
        configure()
        self.addCleanup(configure)
        self.lines = []
        self.runs = []

    def run_(self, command, **kw):
        self.runs.append((command, kw))
        return done(command)

    def test_items_run_the_router_once_with_an_argument_list(self):
        rc.route([("item", 9), ("item", 3)], lambda: None,
                 self.lines.append, self.run_, env={"FORGE_CRON_CHECKOUT":
                                                    "/c"})
        (command, kw), = self.runs
        self.assertEqual(command[1:], ["forge-router", "--send", "--only",
                                       "3,9"])
        self.assertEqual(command[0], str(ROOT / "bin" / "forge-cron-run"))
        self.assertNotIn("shell", kw)
        self.assertEqual(kw["env"], {"FORGE_CRON_CHECKOUT": "/c"})

    def test_tools_tick_in_order_before_the_router(self):
        rc.route([("item", 4), ("tool", MERGER), ("tool", BUILD)],
                 lambda: None, self.lines.append, self.run_)
        self.assertEqual([c[1:] for c, _ in self.runs], [
            list(BUILD), list(MERGER),
            ["forge-router", "--send", "--only", "4"]])

    def test_a_tool_alone_starts_no_router(self):
        rc.route([("tool", MERGER)], lambda: None, self.lines.append,
                 self.run_)
        self.assertEqual([c[1:] for c, _ in self.runs], [list(MERGER)])

    def test_each_tool_runs_through_the_cron_runner_as_configured(self):
        tools = [t for ts in example()["receiver"]["ticks"].values()
                 for t in ts]
        for tool in tools:
            argv = [tool] if isinstance(tool, str) else tool
            self.runs.clear()
            rc.route([("tool", tuple(argv))], lambda: None,
                     self.lines.append, self.run_)
            self.assertEqual(self.runs[0][0],
                             [str(ROOT / "bin" / "forge-cron-run")] + argv)

    def test_the_example_s_forge_tools_exist_here(self):
        receiver = example()["receiver"]
        names = [t if isinstance(t, str) else t[0]
                 for ts in receiver["ticks"].values() for t in ts]
        for name in names + [receiver["route"][0]]:
            if name.startswith("forge-"):
                self.assertTrue((ROOT / "bin" / name).exists(), name)

    def test_a_commit_is_looked_up_and_merged_with_items(self):
        gh = FakeGitHub([
            {"number": 72, "state": "open", "head": {"sha": SHA}},
            {"number": 70, "state": "closed", "head": {"sha": SHA}},
            {"number": 71, "state": "open", "head": {"sha": "f" * 40}}])
        rc.route([("commit", SHA), ("item", 3)], lambda: gh,
                 self.lines.append, self.run_)
        self.assertEqual(gh.calls, [("repos/example/widget/commits/%s/pulls"
                                     % SHA,)])
        self.assertEqual(self.runs[0][0][-1], "3,72")

    def test_no_token_leaves_commits_to_cron(self):
        rc.route([("commit", SHA)], lambda: None, self.lines.append,
                 self.run_)
        self.assertEqual(self.runs, [])
        self.assertTrue(self.lines[0].startswith("NO-TOKEN: "))

    def test_items_need_no_token(self):
        def no_mint():
            raise AssertionError("minted for items alone")
        rc.route([("item", 1)], no_mint, self.lines.append, self.run_)
        self.assertEqual(len(self.runs), 1)

    def test_a_failed_lookup_is_reported_and_dropped(self):
        gh = FakeGitHub(RuntimeError("gh api: 502"))
        rc.route([("commit", SHA)], lambda: gh, self.lines.append,
                 self.run_)
        self.assertEqual(self.runs, [])
        self.assertTrue(self.lines[0].startswith("LOOKUP-FAILED: "))

    def test_a_leading_tilde_is_the_run_s_home(self):
        configure(ticks={"push": [["forge-merger", "--state-dir",
                                   "~/m", "a~b"]]},
                  route=["forge-router", "--state-dir", "~/r", "--only",
                         "{items}", "~"])
        rc.route([("tool", ("forge-merger", "--state-dir", "~/m", "a~b")),
                  ("item", 2)], lambda: None, self.lines.append, self.run_,
                 env={"HOME": "/h"})
        self.assertEqual([c[1:] for c, _ in self.runs], [
            ["forge-merger", "--state-dir", "/h/m", "a~b"],
            ["forge-router", "--state-dir", "/h/r", "--only", "2", "/h"]])

    def test_each_run_has_the_timeout(self):
        configure(run_timeout=42)
        rc.route([("tool", MERGER), ("item", 1)], lambda: None,
                 self.lines.append, self.run_)
        self.assertEqual([kw["timeout"] for _, kw in self.runs], [42, 42])
        configure()
        self.assertEqual(rc.RUN_TIMEOUT, 600)

    def test_a_run_over_its_time_is_reported_and_the_rest_go_on(self):
        def slow(command, **kw):
            self.runs.append((command, kw))
            return done(command, rc=rc.TIMED_OUT) \
                if "forge-pr-check" in command else done(command)
        rc.route([("tool", BUILD), ("tool", MERGER), ("item", 1)],
                 lambda: None, self.lines.append, slow)
        self.assertEqual(len(self.runs), 3)
        self.assertIn("TIMEOUT: forge-pr-check stopped after 600s",
                      self.lines)
        self.assertIn("forge-merger exit 0", self.lines)

    def test_the_configuration_checks_the_timeout(self):
        data = example()
        data["receiver"]["run_timeout"] = 0
        self.assertIn("receiver.run_timeout must be at least 1",
                      config.check(data))

    def test_without_a_route_nothing_is_routed_or_minted(self):
        configure(route=None)

        def no_mint():
            raise AssertionError("minted with no route")
        rc.route([("item", 1), ("commit", SHA), ("tool", MERGER)], no_mint,
                 self.lines.append, self.run_)
        self.assertEqual([c[1:] for c, _ in self.runs], [list(MERGER)])


class LookupTest(unittest.TestCase):
    def test_reads_as_the_token_and_raises_on_refusal(self):
        calls = []

        def call(url, token, scheme="Bearer", method="GET", body=None):
            calls.append((url, token, scheme, method))
            return (200, [{"number": 1}]) if len(calls) == 1 else (502, {})
        lookup = rc.Lookup("tok", call)
        self.assertEqual(lookup.api("repos/example/widget/commits/x/pulls"),
                         [{"number": 1}])
        self.assertTrue(calls[0][0].endswith(
            "/repos/example/widget/commits/x/pulls"))
        self.assertEqual(calls[0][1:], ("tok", "token", "GET"))
        with self.assertRaises(RuntimeError):
            lookup.api("repos/example/widget/commits/x/pulls")


class SecretTest(unittest.TestCase):
    def write(self, text, mode):
        handle, path = tempfile.mkstemp()
        os.write(handle, text)
        os.close(handle)
        os.chmod(path, mode)
        self.addCleanup(os.unlink, path)
        return path

    def test_reads_a_private_file(self):
        self.assertEqual(rc.load_secret(self.write(SECRET + b"\n", 0o600)),
                         SECRET)

    def test_refuses_a_file_others_can_read(self):
        with self.assertRaises(ValueError) as caught:
            rc.load_secret(self.write(SECRET, 0o644))
        self.assertNotIn(SECRET.decode(), str(caught.exception))

    def test_refuses_empty_and_missing(self):
        with self.assertRaises(ValueError):
            rc.load_secret(self.write(b"\n", 0o600))
        with self.assertRaises(ValueError):
            rc.load_secret("/nonexistent/secret")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(configure)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.checkout = os.path.join(tmp.name, "widget-cron")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        data = example()
        data["receiver"]["address"] = "127.0.0.1"
        data["receiver"]["secret_file"] = "~/secret"
        with open(os.path.join(self.checkout, config.CONFIG_NAME),
                  "w") as handle:
            json.dump(data, handle)
        self.lines = []

    def main(self, argv, **env):
        environ = dict({"HOME": self.home,
                        "FORGE_CRON_CHECKOUT": self.checkout}, **env)
        return rc.main(argv, out=self.lines.append, environ=environ)

    def test_will_not_start_without_a_secret(self):
        got = self.main(["--port", "0"],
                        FORGE_RECEIVER_SECRET_FILE="/nonexistent/secret")
        self.assertEqual(got, 2)
        self.assertTrue(self.lines[0].startswith("NO-SECRET: "))

    def test_the_secret_file_is_the_configuration_s_under_home(self):
        self.assertEqual(self.main(["--port", "0"]), 2)
        self.assertEqual(self.lines, ["NO-SECRET: %s: No such file or "
                                      "directory"
                                      % os.path.join(self.home, "secret")])

    def test_will_not_start_without_a_checkout_or_configuration(self):
        self.assertEqual(self.main([], FORGE_CRON_CHECKOUT=""), 2)
        self.assertTrue(self.lines[-1].startswith("NO-REPO: "))
        self.assertEqual(self.main(["--checkout", self.home]), 2)
        self.assertTrue(self.lines[-1].startswith("NO-CONFIG: "))

    def test_will_not_start_on_a_port_taken(self):
        with open(os.path.join(self.home, "secret"), "wb") as handle:
            handle.write(SECRET)
        os.chmod(os.path.join(self.home, "secret"), 0o600)
        blocker = socket.socket()
        self.addCleanup(blocker.close)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        self.assertEqual(self.main([], FORGE_RECEIVER_PORT=str(port)), 2)
        self.assertTrue(self.lines[-1].startswith(
            "NO-PORT: 127.0.0.1:%d: " % port))

    def test_a_changed_configuration_exits_0_and_ids_persist(self):
        secret = os.path.join(self.home, "secret")
        with open(secret, "wb") as handle:
            handle.write(SECRET)
        os.chmod(secret, 0o600)
        state = os.path.join(self.home, "state")
        conf = os.path.join(self.checkout, config.CONFIG_NAME)

        def touch():
            with open(conf) as handle:
                data = handle.read()
            os.remove(conf)
            with open(conf, "w") as handle:
                handle.write(data)
        timer = threading.Timer(0.2, touch)
        self.addCleanup(timer.cancel)
        timer.start()
        with unittest.mock.patch.object(rc, "WATCH_S", 0.05):
            got = self.main(["--port", "0"], FORGE_STATE_DIR=state)
        self.assertEqual(got, 0)
        self.assertTrue(any(line.startswith("RESTART: %s changed" % conf)
                            for line in self.lines), self.lines)
        self.assertTrue(os.path.isdir(os.path.join(state, "widget",
                                                   "receiver")))


class UnitTest(unittest.TestCase):
    def test_the_unit_runs_the_installed_copy_on_the_cron_checkout(self):
        unit = (ROOT / "examples" / "forge-receiver.service").read_text()
        self.assertIn("ExecStart=AGENT_FORGE/bin/forge-receiver\n", unit)
        self.assertIn("Environment=FORGE_CRON_CHECKOUT=CRON_CHECKOUT\n",
                      unit)
        self.assertIn("Restart=always\n", unit)


if __name__ == "__main__":
    unittest.main()


class InstalledBinTest(unittest.TestCase):
    """A receiver runs for days, across installs. forge-install points
    a stable link at a new versioned directory and removes the old one,
    so the tools must be found through the link on each event, not
    where the receiver started (every tick exited 127 after an
    install)."""

    def test_the_tools_follow_a_new_install(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("v1", "v2"):
                os.makedirs(os.path.join(tmp, "d", version, "bin"))
            link = os.path.join(tmp, "agent-forge")
            os.symlink(os.path.join("d", "v1"), link)
            here = os.path.join(link, "bin", "forge-receiver")
            with unittest.mock.patch.object(rc, "__file__", here):
                first = rc.cron_command(["forge-merger"])[0]
                os.remove(link)
                os.symlink(os.path.join("d", "v2"), link)
                second = rc.cron_command(["forge-merger"])[0]
        self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(
            first))), "v1")
        self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(
            second))), "v2")


class SeenUnwritable(unittest.TestCase):
    def test_a_state_directory_it_cannot_write_loses_only_persistence(self):
        """A full disk or a missing directory never loses the delivery:
        `first` still answers, from memory."""
        with tempfile.TemporaryDirectory() as root:
            seen = rc.Seen(path=os.path.join(root, "gone", "seen.json"))
            self.assertTrue(seen.first("a"))
            self.assertFalse(seen.first("a"))
