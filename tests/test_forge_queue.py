"""Tests for bin/forge-queue: queued slow work that tells its agent when
it is done. systemd-run and the relay are faked; git, flock and the
jobs themselves are real, in temporary directories."""

import datetime
import json
import os
import subprocess
import threading
import time
import unittest

from agent_forge import state
from tests.helpers import Repo, done, example, load_tool

fq = load_tool("forge-queue")

T0 = datetime.datetime(2026, 10, 21, 8, 15, tzinfo=datetime.timezone.utc)


class QueueTest(unittest.TestCase):
    """A project with queues, a git repository to submit from, a fake
    systemd-run and a fake relay."""

    queues = {"gate": {}, "pair": {"slots": 2},
              "kept": {"keep_worktree": True},
              "short": {"timeout_minutes": 1}}

    def setUp(self):
        self.r = Repo(self)
        self.sd = os.path.join(self.r.dir, "state")
        data = example()
        data["queue"] = {"queues": self.queues, "state_dir": self.sd}
        self.config = os.path.join(self.r.dir, "config.json")
        with open(self.config, "w") as f:
            json.dump(data, f)
        self.repo = os.path.join(self.r.dir, "repo")
        self.r.git(self.r.dir, "init", "-q", "-b", "main", self.repo)
        self.r.git(self.repo, "config", "user.name", "Tester")
        self.r.git(self.repo, "config", "user.email", "t@example.org")
        os.makedirs(os.path.join(self.repo, "sub"))
        self.write("f", "one\n")
        self.write("sub/g", "x\n")
        self.commit("first")
        self.env = dict(self.r.env, FORGE_CONFIG=self.config,
                        FORGE_AGENT="LeadDev")
        self.units = []
        self.sent = []
        self.refuse = []
        self.lines = []
        self.now = T0
        self._saved = {k: getattr(fq, k) for k in (
            "WAIT_POLL_S", "JOB_POLL_S", "MINUTE_S", "UNTIMED")}
        fq.WAIT_POLL_S, fq.JOB_POLL_S = 0.02, 0.01
        fq.UNTIMED = os.path.join(self.r.dir, "no-forge-untimed")
        self.addCleanup(lambda: [setattr(fq, k, v)
                                 for k, v in self._saved.items()])

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def commit(self, message):
        self.r.git(self.repo, "add", "-A")
        self.r.git(self.repo, "commit", "-q", "-m", message)
        return self.r.git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def run_fake(self, args, cwd=None, env=None, timeout=None):
        if args[0] in ("systemd-run", "systemctl"):
            self.units.append(args)
            if args[0] == "systemd-run" and self.unit_fails:
                return done(args, 1, "", "Failed to connect to bus")
            return done(args)
        return subprocess.run(args, cwd=cwd, env=env or self.r.env,
                              capture_output=True, text=True)

    unit_fails = False

    def send(self, job, text):
        self.sent.append((job["agent"], text))
        return self.refuse.pop(0) if self.refuse else None

    def clock(self):
        return self.now

    def fake_sleep(self, seconds):
        self.now += datetime.timedelta(seconds=seconds)

    def cli(self, *argv, env=None, cwd=None, real_time=False, out=None):
        return fq.main(list(argv), environ=env or self.env, run=self.run_fake,
                       out=out or self.lines.append,
                       clock=fq.now_utc if real_time else self.clock,
                       sleep=time.sleep if real_time else self.fake_sleep,
                       send=self.send, cwd=cwd or self.repo)

    def submit(self, queue, *command, opts=(), **kw):
        before = len(self.lines)
        self.assertEqual(self.cli("submit", queue, *opts, "--", *command,
                                  **kw), 0, self.lines)
        return self.lines[before]

    def status(self, ticket):
        return state.load(fq.job_file(self.sd, ticket, "status.json"))

    def job(self, ticket):
        return state.load(fq.ticket_path(self.sd, ticket))

    def log(self, ticket):
        with open(fq.job_file(self.sd, ticket, "log")) as f:
            return f.read()

    def run_ticket(self, ticket, **kw):
        return self.cli("--state-dir", self.sd, "run", ticket, **kw)


class Submit(QueueTest):

    def test_ticket_and_unit(self):
        ticket = self.submit("gate", "make", "gate")
        self.assertRegex(ticket, r"^gate-20261021T081500Z-[0-9a-f]{4}$")
        self.assertEqual(fq.queue_of(ticket), "gate")
        unit = self.units[-1]
        self.assertEqual(unit[:6], ["systemd-run", "--user", "--unit",
                                    "forge-queue-" + ticket, "--collect",
                                    "--quiet"])
        self.assertEqual(unit[-4:], ["--state-dir", self.sd, "run", ticket])
        job = self.job(ticket)
        head = self.r.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual((job["agent"], job["command"], job["commit"],
                          job["name"], job["sessions"]),
                         ("LeadDev", ["make", "gate"], head, "make gate",
                          ["WidgetLeadDev"]))
        self.assertEqual(job["settings"], {"slots": 1, "nice": 10,
                                           "timeout_minutes": 120,
                                           "keep_worktree": False})
        self.assertEqual(os.stat(fq.ticket_path(self.sd, ticket)).st_mode
                         & 0o777, 0o600)
        self.assertEqual(self.status(ticket)["state"], "queued")

    def test_options_before_the_command(self):
        ticket = self.submit("gate", "make", "-j", "4", opts=(
            "--name", "the gate", "--agent", "UIDev"))
        job = self.job(ticket)
        self.assertEqual((job["name"], job["agent"], job["command"]),
                         ("the gate", "UIDev", ["make", "-j", "4"]))

    def test_a_dirty_worktree_is_refused_naming_the_files(self):
        self.write("f", "changed\n")
        self.write("new", "untracked\n")
        self.assertEqual(self.cli("submit", "gate", "--", "true"), 2)
        text = "\n".join(self.lines)
        self.assertIn("  f", text)
        self.assertIn("  new", text)
        self.assertEqual(self.units, [])
        self.assertEqual(os.listdir(os.path.join(self.sd)), [])

    def test_dir_runs_as_it_stands_even_when_dirty(self):
        self.write("f", "changed\n")
        bundle = os.path.join(self.r.dir, "bundle")
        os.makedirs(bundle)
        ticket = self.submit("gate", "pwd", opts=("--dir", bundle))
        job = self.job(ticket)
        self.assertEqual((job["dir"], job["commit"], job["worktree"]),
                         (bundle, None, None))

    def test_refusals(self):
        self.assertEqual(self.cli("submit", "nope", "--", "true"), 2)
        self.assertIn("no queue 'nope'", self.lines[-1])
        self.assertEqual(self.cli("submit", "gate", "--agent", "Nobody",
                                  "--", "true"), 2)
        self.assertIn("Nobody is not one", self.lines[-1])
        self.assertEqual(self.cli("submit", "gate"), 2)
        self.assertEqual(self.cli("submit", "gate", "--at", "HEAD",
                                  "--dir", "/", "--", "true"), 2)
        self.assertEqual(self.cli("submit", "gate", "--at", "nosuch",
                                  "--", "true"), 2)
        self.assertEqual(self.units, [])

    def test_no_agent(self):
        env = dict(self.env)
        del env["FORGE_AGENT"]
        self.r.git(self.repo, "config", "--unset", "user.name")
        self.assertEqual(self.cli("submit", "gate", "--", "true", env=env), 2)
        self.assertIn("no agent", self.lines[-1])

    def test_agent_from_the_worktree_identity(self):
        env = dict(self.env)
        del env["FORGE_AGENT"]
        self.r.git(self.repo, "config", "user.name", "UIDev")
        ticket = self.submit("gate", "true", env=env)
        self.assertEqual(self.job(ticket)["agent"], "UIDev")

    def test_inert_without_a_queue_section(self):
        data = example()
        with open(self.config, "w") as f:
            json.dump(data, f)
        self.assertEqual(self.cli("submit", "gate", "--", "true"), 2)
        self.assertIn("no queue section", self.lines[-1])

    def test_systemd_run_failing_queues_nothing(self):
        self.unit_fails = True
        self.assertEqual(self.cli("submit", "gate", "--", "true"), 3)
        self.assertIn("Failed to connect to bus", self.lines[-1])
        self.lines.clear()
        self.cli("list")
        self.assertEqual(self.lines, ["nothing queued"])
        ticket = os.listdir(os.path.join(self.sd, "gate"))
        ticket = [t for t in ticket if t.endswith(".json") and
                  t.startswith("gate-")][0][:-5]
        self.assertEqual(self.status(ticket)["state"], "failed")
        self.assertFalse(os.path.exists(fq.job_file(self.sd, ticket,
                                                    "worktree")))
        self.assertEqual(self.sent, [])


class Running(QueueTest):

    def test_at_freezes_the_commit(self):
        first = self.r.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        ticket = self.submit("gate", "cat", "f")
        self.write("f", "two\n")
        self.commit("second")
        self.assertEqual(self.run_ticket(ticket), 0)
        self.assertEqual(self.log(ticket), "one\n")
        st = self.status(ticket)
        self.assertEqual((st["state"], st["exit"], st["delivered"]),
                         ("done", 0, True))
        self.assertEqual(self.job(ticket)["commit"], first)
        # The frozen worktree is gone, and git forgot it.
        self.assertFalse(os.path.exists(fq.job_file(self.sd, ticket,
                                                    "worktree")))
        listed = self.r.git(self.repo, "worktree", "list").stdout
        self.assertNotIn(ticket, listed)
        self.assertEqual(self.sent[0][0], "LeadDev")
        self.assertTrue(self.sent[0][1].startswith(
            "Queue gate: cat f ended, exit 0 (green), after 0 min waiting "
            "and 0 min running.\nTicket %s, commit %s. Log: " % (
                ticket, first[:8])))

    def test_runs_in_the_subdirectory_it_was_submitted_from(self):
        ticket = self.submit("kept", "pwd", cwd=os.path.join(self.repo, "sub"))
        self.run_ticket(ticket)
        self.assertEqual(self.log(ticket).strip(), os.path.join(
            fq.job_file(self.sd, ticket, "worktree"), "sub"))
        # keep_worktree: still there.
        self.assertTrue(os.path.isdir(fq.job_file(self.sd, ticket,
                                                  "worktree")))

    def test_environment_summary_and_red(self):
        ticket = self.submit("gate", "sh", "-c",
                             'echo "$FORGE_QUEUE_TICKET $FORGE_AGENT"; '
                             'echo "progress" > "$FORGE_QUEUE_SUMMARY"; '
                             'echo "series-x a46b5dfe exit 3, results in '
                             '~/results/t123" >> "$FORGE_QUEUE_SUMMARY"; '
                             "exit 3", opts=("--name", "series x"))
        self.run_ticket(ticket)
        self.assertEqual(self.log(ticket), "%s LeadDev\n" % ticket)
        text = self.sent[0][1].splitlines()
        self.assertEqual(text[0], "Queue gate: series x ended, exit 3 (red), "
                         "after 0 min waiting and 0 min running.")
        self.assertEqual(text[2],
                         "series-x a46b5dfe exit 3, results in ~/results/t123")

    def test_a_command_that_cannot_start(self):
        ticket = self.submit("gate", "/nonexistent/command")
        self.run_ticket(ticket)
        st = self.status(ticket)
        self.assertEqual(st["state"], "done")
        self.assertNotEqual(st["exit"], 0)

    def test_timeout(self):
        fq.MINUTE_S = 0.05
        ticket = self.submit("short", "sleep", "30")
        started = time.monotonic()
        self.run_ticket(ticket, real_time=True)
        self.assertLess(time.monotonic() - started, 10)
        st = self.status(ticket)
        self.assertEqual((st["state"], st["exit"]), ("timed-out", -15))
        self.assertIn("short: sleep 30 timed out at its 1-minute limit (red)",
                      self.sent[0][1])

    def test_a_second_runner_does_nothing(self):
        ticket = self.submit("gate", "true")
        held = fq.hold_ticket(self.sd, ticket)
        self.addCleanup(held.close)
        self.assertEqual(self.run_ticket(ticket), 0)
        self.assertIn("already has a runner", self.lines[-1])
        self.assertEqual(self.status(ticket)["state"], "queued")

    def test_unknown_ticket(self):
        self.assertEqual(self.run_ticket("gate-20261021T081500Z-0000"), 2)
        self.assertEqual(self.cli("status", "nonsense"), 2)

    def test_untimed_seam(self):
        job = {"ticket": "gate-20261021T081500Z-3fa2",
               "command": ["make", "gate"], "settings": {"nice": 5}}
        self.assertEqual(fq.job_argv(job), ["nice", "-n", "5", "make", "gate"])
        untimed = os.path.join(self.r.dir, "forge-untimed")
        with open(untimed, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(untimed, 0o755)
        self.assertEqual(fq.job_argv(job, untimed), [
            "nice", "-n", "5", untimed, "--name", job["ticket"], "--",
            "make", "gate"])


class Order(QueueTest):
    """Real runners in threads, flocks and all."""

    def runners(self, tickets, record):
        threads = []
        for ticket in reversed(tickets):
            t = threading.Thread(target=self.run_ticket, args=(ticket,),
                                 kwargs={"real_time": True,
                                         "out": lambda line: None})
            t.start()
            threads.append(t)
            time.sleep(0.05)
        for t in threads:
            t.join(60)
            self.assertFalse(t.is_alive())
        with open(record) as f:
            return [ln.split() for ln in f.read().splitlines()]

    def job_command(self, record):
        return ("sh", "-c", 'echo "$FORGE_QUEUE_TICKET start" >> %s; '
                'sleep 0.3; echo "$FORGE_QUEUE_TICKET end" >> %s'
                % (record, record))

    def test_one_slot_runs_in_submit_order_one_at_a_time(self):
        record = os.path.join(self.r.dir, "record")
        tickets = [self.submit("gate", *self.job_command(record),
                               real_time=True) for _ in range(3)]
        events = self.runners(tickets, record)
        self.assertEqual(events, [[t, e] for t in tickets
                                  for e in ("start", "end")])
        self.assertEqual(len(self.sent), 3)

    def test_two_slots_start_in_order_two_at_a_time(self):
        record = os.path.join(self.r.dir, "record")
        tickets = [self.submit("pair", *self.job_command(record),
                               real_time=True) for _ in range(4)]
        events = self.runners(tickets, record)
        self.assertEqual([t for t, e in events if e == "start"], tickets)
        running = peak = 0
        for _, e in events:
            running += 1 if e == "start" else -1
            peak = max(peak, running)
        self.assertEqual(peak, 2)

    def test_a_dead_older_waiter_does_not_block(self):
        record = os.path.join(self.r.dir, "record")
        old = self.submit("gate", "true", real_time=True)
        new = self.submit("gate", *self.job_command(record), real_time=True)
        # The older ticket's runner died while waiting.
        state.save(fq.job_file(self.sd, old, "status.json"),
                   {"state": "waiting", "queued_at": fq.stamp(fq.now_utc())})
        self.run_ticket(new, real_time=True)
        self.assertEqual(self.status(new)["state"], "done")


class Cancel(QueueTest):

    def wait_for(self, ticket, wanted):
        for _ in range(500):
            if self.status(ticket).get("state") == wanted:
                return
            time.sleep(0.01)
        self.fail("%s never became %s" % (ticket, wanted))

    def test_cancel_a_running_job(self):
        ticket = self.submit("gate", "sleep", "30", real_time=True)
        t = threading.Thread(target=self.run_ticket, args=(ticket,),
                             kwargs={"real_time": True,
                                     "out": lambda line: None})
        t.start()
        self.wait_for(ticket, "running")
        self.assertEqual(self.cli("cancel", ticket), 0)
        self.assertIn("within seconds", self.lines[-1])
        t.join(30)
        st = self.status(ticket)
        self.assertEqual(st["state"], "cancelled")
        self.assertIn("gate: sleep 30 was cancelled, after", self.sent[0][1])
        self.assertIn("running.", self.sent[0][1].splitlines()[0])
        self.assertEqual(self.cli("cancel", ticket), 1)

    def test_cancel_a_waiting_job(self):
        first = self.submit("gate", "sleep", "30", real_time=True)
        second = self.submit("gate", "true", real_time=True)
        slot = fq.locks.try_lock(fq.slot_path(self.sd, "gate", 0), "other")
        self.addCleanup(slot.close)
        t = threading.Thread(target=self.run_ticket, args=(first,),
                             kwargs={"real_time": True,
                                     "out": lambda line: None})
        t.start()
        self.wait_for(first, "waiting")
        self.assertEqual(self.cli("cancel", first), 0)
        t.join(30)
        self.assertEqual(self.status(first)["state"], "cancelled")
        self.assertNotIn("started", self.status(first))
        self.assertEqual(self.sent[0][1].splitlines()[0],
                         "Queue gate: sleep 30 was cancelled, after 0 min "
                         "waiting.")
        self.assertEqual(self.status(second)["state"], "queued")

    def test_cancel_with_no_runner(self):
        ticket = self.submit("gate", "true")
        worktree = fq.job_file(self.sd, ticket, "worktree")
        self.assertTrue(os.path.isdir(worktree))
        self.assertEqual(self.cli("cancel", ticket), 0)
        self.assertEqual(self.status(ticket)["state"], "cancelled")
        self.assertFalse(os.path.exists(worktree))
        self.assertEqual(len(self.sent), 1)
        # A runner that starts after all finds it ended.
        self.assertEqual(self.run_ticket(ticket), 0)
        self.assertEqual(len(self.sent), 1)

    def test_only_her_own_or_the_operators(self):
        ticket = self.submit("gate", "true")
        env = dict(self.env, FORGE_AGENT="UIDev")
        self.assertEqual(self.cli("cancel", ticket, env=env), 2)
        self.assertIn("LeadDev's", self.lines[-1])
        self.assertEqual(self.cli("cancel", ticket, "--operator", env=env), 0)


class Recover(QueueTest):

    def test_lost_after_a_restart_and_waiting_started_again(self):
        running = self.submit("gate", "true")
        waiting = self.submit("gate", "true")
        queued = self.submit("gate", "true")
        fresh = self.submit("gate", "true")
        state.save(fq.job_file(self.sd, running, "status.json"),
                   {"state": "running", "started": fq.stamp(T0)})
        state.save(fq.job_file(self.sd, waiting, "status.json"),
                   {"state": "waiting", "queued_at": fq.stamp(T0)})
        self.now = T0 + datetime.timedelta(minutes=10)
        state.save(fq.job_file(self.sd, fresh, "status.json"),
                   {"state": "queued", "queued_at": fq.stamp(self.now)})
        self.units.clear()
        self.assertEqual(self.cli("recover"), 0)
        self.assertEqual(self.status(running)["state"], "lost")
        self.assertFalse(os.path.exists(fq.job_file(self.sd, running,
                                                    "worktree")))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("gate: true was lost",
                      self.sent[0][1])
        # Started again in their order; the fresh one is left alone.
        self.assertEqual([u[3] for u in self.units],
                         ["forge-queue-" + waiting, "forge-queue-" + queued])
        self.assertEqual(self.status(waiting)["state"], "queued")
        self.assertEqual(self.status(waiting)["restarts"], 1)
        # Started again, they hold their places for the grace period.
        self.units.clear()
        self.cli("recover")
        self.assertEqual(self.units, [])
        self.assertEqual(len(self.sent), 1)

    def test_an_undelivered_message_is_retried_and_listed(self):
        ticket = self.submit("gate", "true")
        self.refuse = ["HTTP 404 unknown-name: no session WidgetLeadDev"]
        # The runner tries once, and again remind_s later.
        self.run_ticket(ticket)
        self.assertEqual(len(self.sent), 2)
        st = self.status(ticket)
        self.assertTrue(st["delivered"])
        self.assertEqual(st["deliveries"], 2)

    def test_recover_retries_what_a_gone_runner_left(self):
        ticket = self.submit("gate", "true")
        state.save(fq.job_file(self.sd, ticket, "status.json"), {
            "state": "done", "exit": 0, "ended": fq.stamp(T0),
            "started": fq.stamp(T0), "waited_s": 0, "ran_s": 0,
            "delivered": False, "delivery_error": "relay unreachable",
            "next_delivery": fq.stamp(T0 + datetime.timedelta(hours=2))})
        self.cli("list", "--undelivered")
        self.assertIn("NOT TOLD: relay unreachable", self.lines[-1])
        self.assertEqual(self.sent, [])
        self.now = T0 + datetime.timedelta(hours=2)
        self.refuse = ["relay unreachable: refused"]
        self.cli("recover")
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.status(ticket)["delivered"])
        self.cli("recover")
        self.assertEqual(len(self.sent), 1)
        self.now += datetime.timedelta(hours=2)
        self.lines.clear()
        self.cli("list", "--undelivered")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.lines[-1], "nothing undelivered")

    def test_old_tickets_are_pruned(self):
        ticket = self.submit("kept", "true")
        self.run_ticket(ticket)
        self.assertTrue(os.path.isdir(fq.job_file(self.sd, ticket,
                                                  "worktree")))
        self.now = T0 + datetime.timedelta(days=6)
        self.cli("recover")
        self.assertTrue(os.path.exists(fq.ticket_path(self.sd, ticket)))
        self.now = T0 + datetime.timedelta(days=8)
        self.cli("recover")
        self.assertFalse(os.path.exists(fq.ticket_path(self.sd, ticket)))
        self.assertFalse(os.path.exists(fq.job_dir(self.sd, ticket)))
        self.assertNotIn(ticket, self.r.git(self.repo, "worktree",
                                            "list").stdout)


class Looking(QueueTest):

    def test_list_status_and_done(self):
        ended = self.submit("gate", "echo", "hello")
        self.run_ticket(ended)
        running = self.submit("gate", "true")
        waiting = self.submit("gate", "true", opts=("--name", "second"))
        state.save(fq.job_file(self.sd, running, "status.json"),
                   {"state": "running", "started": fq.stamp(T0)})
        # Its runner is alive, so it isn't lost.
        held = fq.hold_ticket(self.sd, running)
        self.addCleanup(held.close)
        self.now = T0 + datetime.timedelta(minutes=31)
        state.save(fq.job_file(self.sd, waiting, "status.json"),
                   {"state": "queued", "queued_at": fq.stamp(self.now)})
        self.lines.clear()
        self.cli("list")
        self.assertEqual(len(self.lines), 2)
        self.assertTrue(self.lines[0].startswith("running    " + running))
        self.assertIn("running 31 min", self.lines[0])
        self.assertTrue(self.lines[1].startswith("queued     " + waiting))
        self.assertIn("LeadDev  second", self.lines[1])
        self.lines.clear()
        self.cli("list", "gate", "--done")
        self.assertEqual(len(self.lines), 3)
        self.assertIn("exit 0", self.lines[2])
        self.lines.clear()
        self.cli("list", "pair")
        self.assertEqual(self.lines, ["nothing queued"])
        self.lines.clear()
        self.assertEqual(self.cli("status", ended), 0)
        shown = json.loads("\n".join(self.lines[:-2]))
        self.assertEqual((shown["state"], shown["exit"], shown["ticket"]),
                         ("done", 0, ended))
        self.assertEqual(self.lines[-1], "  hello")


class FakeRelay:
    """xmpp-webhook-relay's POST /agent/<name>, taking every message."""

    def __init__(self, test):
        import http.server
        posts = self.posts = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                posts.append((self.path, self.headers["X-Webhook-Token"],
                              body.decode()))
                answer = json.dumps({"status": "queued",
                                     "targets": ["x"]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                      Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        test.addCleanup(thread.join)
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


def user_manager():
    try:
        got = subprocess.run(["systemctl", "--user", "is-system-running"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return got.stdout.strip() in ("running", "degraded")


class EndToEnd(QueueTest):
    """Three jobs in a one-slot queue, each runner its own process, told
    through a fake relay with the real poster. Under a user manager the
    runners are real systemd units; without one, the fake systemd-run
    starts the same command line as a plain process."""

    def setUp(self):
        super().setUp()
        self.relay = FakeRelay(self)
        token = os.path.join(self.r.dir, "token")
        with open(token, "w") as f:
            f.write("WEBHOOK_TOKEN=secret\n")
        with open(self.config) as f:
            data = json.load(f)
        data["relay"].update(url=self.relay.url, token_file=token)
        data["agents"]["LeadDev"]["twins"] = ["LeadDevTwin"]
        with open(self.config, "w") as f:
            json.dump(data, f)
        self.children = []

    def start_process(self, args, cwd=None, env=None, timeout=None):
        if args[0] != "systemd-run":
            return self.run_fake(args, cwd, env, timeout)
        command = args[args.index("--description") + 2:]
        self.children.append(subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return done(args)

    def three_jobs(self, run):
        record = os.path.join(self.r.dir, "record")
        tickets = []
        for n in range(3):
            tickets.append(None)
            got = fq.main(["submit", "gate", "--name", "job %d" % n, "--",
                           "sh", "-c", 'echo "$FORGE_QUEUE_TICKET" >> %s; '
                           'echo "log of %d"; sleep 0.2' % (record, n)],
                          environ=self.env, run=run, out=self.lines.append,
                          cwd=self.repo)
            self.assertEqual(got, 0, self.lines)
            tickets[-1] = self.lines[-1]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not all(
                self.status(t).get("delivered") for t in tickets):
            time.sleep(0.1)
        for child in self.children:
            child.wait(30)
        with open(record) as f:
            self.assertEqual(f.read().split(), tickets)
        for n, ticket in enumerate(tickets):
            self.assertEqual(self.log(ticket), "log of %d\n" % n)
        self.assertEqual(len(self.relay.posts), 3)
        for (path, token, text), ticket in zip(
                sorted(self.relay.posts, key=lambda p: p[2]), tickets):
            self.assertEqual((path, token), ("/agent/WidgetLeadDev",
                                             "secret"))
            self.assertIn("Ticket %s" % ticket, text)

    def test_runner_processes(self):
        self.three_jobs(self.start_process)

    @unittest.skipUnless(user_manager(), "no systemd user manager here")
    def test_under_a_real_user_manager(self):
        self.three_jobs(fq.proc.sh)


class Message(unittest.TestCase):

    def test_the_designs_text(self):
        job = {"ticket": "gate-20261021T081500Z-3fa2", "queue": "gate",
               "name": "full gate", "commit": "1a2b3c4d5e6f",
               "dir": "/x", "submitted": "2026-10-21T08:15:00Z",
               "settings": {"timeout_minutes": 120}}
        status = {"state": "done", "exit": 0, "waited_s": 12 * 60,
                  "started": "x", "ran_s": 31 * 60 + 10}
        self.assertEqual(fq.message("/s", job, status), (
            "Queue gate: full gate ended, exit 0 (green), after 12 min "
            "waiting and 31 min running.\n"
            "Ticket gate-20261021T081500Z-3fa2, commit 1a2b3c4d. "
            "Log: /s/gate/gate-20261021T081500Z-3fa2/log"))
        status.update(exit=-9)
        self.assertIn("full gate was killed by signal 9 (red)",
                      fq.message("/s", job, status))
        job.update(commit=None)
        status = {"state": "lost", "ended": "2026-10-21T09:15:00Z"}
        text = fq.message("/s", job, status)
        self.assertIn("was lost", text)
        self.assertIn("after 60 min waiting.", text)
        self.assertIn("Ticket gate-20261021T081500Z-3fa2, in /x.", text)


if __name__ == "__main__":
    unittest.main()
