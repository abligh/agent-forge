"""Tests for bin/forge-closing-keywords.

The fact the tool rests on: **GitHub ignores negation.** "fixes #N and
does nothing here" closes #N, so the check matches the keyword and not
the sentiment. A parser that understood "does not" would be wrong in
the same direction as GitHub's, and would pass exactly the cases that
fire. GitHub and git are faked except where a test says it runs real
git; nothing reaches the network.
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

from agent_forge import config
from tests.helpers import EXAMPLE, ROOT, Repo, done, example, load_tool

tool = load_tool("forge-closing-keywords")
TOOL = ROOT / "bin" / "forge-closing-keywords"
REPO = "example/widget"
FAIL = object()
SAME = object()


def project(data=None, **closing):
    data = data or example()
    if closing:
        data["closing_keywords"] = closing
    return config.Project(data, str(EXAMPLE))


def with_shas(log):
    out = []
    for i, part in enumerate(p for p in log.split("\0") if p.strip()):
        out.append("c0ffee%02d %s\x00" % (i, part.strip()))
    return "".join(out)


def parts(log):
    return [p.strip() for p in log.split("\0") if p.strip()]


def full_shas(log):
    return ["c0ffee%02d%s" % (i, "0" * 32) for i, _p in enumerate(parts(log))]


def pr_payload(declared=(), title="t", body="", commits=1):
    return {"data": {"repository": {"pullRequest": {
        "title": title, "body": body, "commits": {"totalCount": commits},
        "closingIssuesReferences": {
            "nodes": [{"number": n} for n in declared]}}}}}


def answering(status, body):
    """A GitHub reader whose every request gets the same answer, and
    which remembers the paths asked."""
    seen = []

    def call(url, token, scheme="Bearer", method="GET", **_):
        seen.append(url.split("://", 1)[1].split("/", 1)[1])
        return status, body
    github = tool.GitHub(None, call)
    github.seen = seen
    return github


class World:
    """git and GitHub as one test sees them.

    `log` is what `git log RANGE` holds (None: unreadable); `pr_log` the
    pull request's commits (default the same; None: unreadable);
    `range_out` and `fresh` what `rev-list RANGE` and `rev-list RANGE
    --not BASE` print (default: the pull request's shas; FAIL: an
    error); `base` the pull request's base (None: unreadable); `facts`
    the GraphQL answer's fields (None: unreadable); `kinds` {number:
    "issue" or "pr"} (None: every number is an issue; a number missing
    from it can't be looked up); `branch` the checked-out branch (None:
    unreadable); `open_pr` that branch's open pull request (None: the
    lookup fails); `below` {number: (merged, [full sha, ...])} for the
    pull requests `--below` names."""

    def __init__(self, log=None, pr_log=SAME, range_out=None, fresh=None,
                 base="main", facts=SAME, kinds=None, branch="mine",
                 open_pr=None, below=None):
        self.below = below or {}
        self.log = log
        self.pr_log = log if pr_log is SAME else pr_log
        self.range_out = range_out
        self.fresh = fresh
        self.base = base
        self.facts = {} if facts is SAME else facts
        self.kinds = kinds
        self.branch = branch
        self.open_pr = open_pr
        self.seen_git, self.seen_api, self.tokens = [], [], set()

    def run(self, args, cwd=None, env=None, timeout=None):
        self.seen_git.append(args)
        if "rev-parse" in args:
            return done(args, 0, self.branch + "\n") if self.branch \
                else done(args, 128)
        if "rev-list" in args:
            out = self.range_out
            if "--not" in args and self.fresh is not None:
                out = self.fresh
            if out is None:
                out = "".join(s + "\n" for s in full_shas(self.pr_log or ""))
            return done(args, 128) if out is FAIL else done(args, 0, out)
        if "log" in args:
            return done(args, 128) if self.log is None \
                else done(args, 0, with_shas(self.log))
        return done(args, 1)

    def call(self, url, token, scheme="Bearer", method="GET", body=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.seen_api.append(path)
        self.tokens.add(token)
        prefix = "/repos/%s/" % REPO
        if path == "/graphql":
            if self.facts is None:
                return 502, {}
            return 200, pr_payload(**self.facts)
        if not path.startswith(prefix):
            return 404, {}
        rest = path[len(prefix):]
        for number, (merged, shas) in self.below.items():
            if rest == "pulls/%d" % number:
                return 200, {"merged_at": "2026-10-25T21:06:31Z"
                             if merged else None}
            if rest.startswith("pulls/%d/commits?" % number):
                return 200, [{"sha": sha, "commit": {"message": "m"}}
                             for sha in shas]
        if rest.startswith("pulls?state=open"):
            if self.open_pr is None:
                return 502, {}
            return 200, [{"number": self.open_pr}] if self.open_pr else []
        if "/commits?" in rest:
            if self.pr_log is None:
                return 502, {}
            page = int(rest.rsplit("page=", 1)[1])
            items = [{"sha": sha, "commit": {"message": message}}
                     for sha, message in zip(full_shas(self.pr_log),
                                             parts(self.pr_log))]
            return 200, items[(page - 1) * 100:page * 100]
        if rest.startswith("pulls/"):
            return (200, {"base": {"ref": self.base}}) if self.base \
                else (404, {})
        if rest.startswith("issues/"):
            number = int(rest.split("/")[1])
            kind = "issue" if self.kinds is None else self.kinds.get(number)
            if kind is None:
                return 404, {}
            issue = {"number": number}
            if kind == "pr":
                issue["pull_request"] = {}
            return 200, issue
        return 404, {}

    def github(self):
        return tool.GitHub(None, self.call)

    def main(self, argv, environ=None, the_project=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tool.main(list(argv), run=self.run, call=self.call,
                             load=lambda **_: the_project or project(),
                             environ={} if environ is None else environ)
        return code, out.getvalue(), err.getvalue()


def the_branch(log, **kw):
    """A world where the range given is exactly the pull request's
    branch."""
    return World(log=log, **kw)


class Closers(unittest.TestCase):
    def test_every_documented_keyword(self):
        for word in ("close", "closes", "closed", "fix", "fixes", "fixed",
                     "resolve", "resolves", "resolved"):
            self.assertEqual(tool.closers("subject\n\n%s #7" % word), {7},
                             "%s should close" % word)

    def test_case_is_ignored(self):
        self.assertEqual(tool.closers("s\n\nFIXES #7"), {7})

    def test_negation_does_not_save_you(self):
        self.assertEqual(tool.closers("s\n\ndoes not close #1159"), {1159})
        self.assertEqual(tool.closers("s\n\nfixes #1072 and does nothing "
                                      "here"), {1072})

    def test_a_bare_reference_is_not_a_closer(self):
        self.assertEqual(tool.closers("s\n\nsee #7, related to #8"), set())

    def test_the_subject_is_excluded_by_default(self):
        self.assertEqual(tool.closers("Fix #7 in the title", body_only=True),
                         set())
        self.assertEqual(tool.closers("Fix #7 in the title",
                                      body_only=False), {7})

    def test_several_in_one_body(self):
        self.assertEqual(tool.closers("s\n\nfixes #1 and closes #2"), {1, 2})


class ReferenceForms(unittest.TestCase):

    def test_the_issue_url_closes(self):
        self.assertEqual(tool.closers(
            "s\n\nfixes https://github.com/example/widget/issues/7"), {7})

    def test_owner_repo_closes(self):
        self.assertEqual(tool.closers("s\n\ncloses example/widget#8"), {8})

    def test_a_colon_and_a_link_now_count_in_a_commit_message(self):
        self.assertEqual(tool.closers("s\n\nFixes: #11"), {11})
        self.assertEqual(tool.closers("s\n\nresolves [#12](https://x/12)"),
                         {12})

    def test_a_pull_request_url_is_not_a_reference(self):
        self.assertEqual(tool.closers(
            "s\n\nfixes https://github.com/example/widget/pull/9"), set())

    def test_another_repository_is_not_this_one(self):
        self.assertEqual(tool.closers("s\n\nfixes other/repo#10"), set())
        self.assertEqual(tool.closers(
            "s\n\nfixes https://github.com/other/repo/issues/10"), set())

    def test_a_denial_spelled_as_a_url_is_refused(self):
        sentence = ("This does not fix "
                    "https://github.com/example/widget/issues/13.")
        self.assertEqual(tool.negated_closers(sentence, {13}),
                         [(13, sentence)])

    def test_the_title_accepts_every_form(self):
        got = tool.will_close({"declared": set(),
                               "title": "Fix example/widget#14 here",
                               "commits": 2}, ["a", "b"])
        self.assertEqual(got["title"], {14})


class TheRepositoryIsTheConfigurations(unittest.TestCase):

    def setUp(self):
        self.addCleanup(tool.configure, project())

    def other(self, **changes):
        data = example()
        data.update(changes)
        tool.configure(project(data))

    def test_its_forms_close_and_the_example_s_do_not(self):
        self.other(repo="other/thing")
        self.assertEqual(tool.closers("s\n\ncloses other/thing#8"), {8})
        self.assertEqual(tool.closers(
            "s\n\nfixes https://github.com/other/thing/issues/9"), {9})
        self.assertEqual(tool.closers("s\n\ncloses example/widget#8"), set())

    def test_a_dot_in_the_name_is_not_a_wildcard(self):
        self.other(repo="other/thing.js")
        self.assertEqual(tool.closers("s\n\ncloses other/thingXjs#8"), set())
        self.assertEqual(tool.closers("s\n\ncloses other/thing.js#8"), {8})

    def test_the_default_range_starts_at_the_default_branch(self):
        world = World(log="s\n\nplain\x00")
        data = example()
        data["default_branch"] = "trunk"
        code, out, _ = world.main([], the_project=project(data))
        self.assertEqual(code, 0)
        self.assertIn("origin/trunk..HEAD", world.seen_git[0])
        self.assertIn("from origin/trunk..HEAD", out)

    def test_the_issues_asked_about_are_the_configured_repository_s(self):
        self.other(repo="other/thing")
        github = answering(200, {"number": 3})
        tool.is_issue(3, github)
        self.assertEqual(github.seen, ["repos/other/thing/issues/3"])


class ADelimiterBesideTheReference(unittest.TestCase):

    INERT = ("(#15)", '"#15"', "'#15'", "*#15*", "_#15_", "~#15~", "<#15>")

    def test_a_delimiter_beside_the_reference_leaves_the_keyword_inert(self):
        for form in self.INERT:
            with self.subTest(form=form):
                self.assertEqual(
                    tool.closers("s\n\ncloses " + form), set(),
                    "GitHub does not close on this form; matching it "
                    "would flag text GitHub ignores")

    def test_the_bracket_is_the_markdown_link_and_not_an_exception(self):
        self.assertEqual(
            tool.closers("s\n\ncloses [#16](https://github.com/example/"
                         "widget/issues/16)"), {16})

    def test_a_bracket_with_no_link_is_a_known_false_positive(self):
        self.assertEqual(tool.closers("s\n\ncloses [#17]"), {17})

    def test_a_squash_subject_suffix_is_not_a_closer(self):
        for subject in ("documentation-standards: the Status list is the "
                        "common cases, not closed (#567)",
                        "README: a sentence denying a close performs the "
                        "close (#556)",
                        "docs: a raised symptom is published at open, not "
                        "at resolve (#38)"):
            with self.subTest(subject=subject):
                self.assertEqual(tool.closers(subject + "\n\nbody\n"), set())

    def test_a_noun_keyword_before_a_citation_list_is_not_a_closer(self):
        for line in ("is a transcript, and #1857's fix (#1863, open) "
                     "makes the default name",
                     "ordering fixes (#1652, #1653) between them.",
                     "internal/pipeline, internal/remediate: two "
                     "doc-comment-contradicts-code fixes (#331, #323)"):
            with self.subTest(line=line):
                self.assertEqual(tool.closers("s\n\n" + line + "\n"), set())


class Declared(unittest.TestCase):
    def test_it_reads_the_graphql_shape(self):
        payload = {"data": {"repository": {"pullRequest": {
            "closingIssuesReferences": {"nodes": [{"number": 1105}]}}}}}
        self.assertEqual(tool.declared(1182, answering(200, payload)),
                         {1105})

    def test_a_failed_call_is_none_not_empty(self):
        self.assertIsNone(tool.declared(1, answering(502, {})))

    def test_garbage_is_none(self):
        self.assertIsNone(tool.declared(1, answering(200, ["nonsense"])))

    def test_a_missing_path_is_none(self):
        self.assertIsNone(tool.declared(1, answering(200, {"data": {}})))

    def test_a_graphql_error_is_none(self):
        self.assertIsNone(tool.declared(1, answering(200, {
            "data": None, "errors": [{"message": "no"}]})))


class Survey(unittest.TestCase):

    def test_undeclared_is_what_it_reports(self):
        world = World(log="subject\n\nfixes #1072 and does nothing here\x00",
                      facts={"declared": [1105]})
        undeclared, _from_commits, known = tool.survey(
            "a..b", pr=1182, run=world.run, github=world.github())
        self.assertEqual(undeclared, {1072})
        self.assertEqual(known, {1105})

    def test_a_declared_closer_is_not_flagged(self):
        world = World(log="subject\n\nFixes #1123.\x00",
                      facts={"declared": [1123]})
        undeclared, _, _ = tool.survey("a..b", pr=1126, run=world.run,
                                       github=world.github())
        self.assertEqual(undeclared, set())

    def test_an_unreadable_range_is_none(self):
        world = World(log=None)
        self.assertIsNone(tool.survey("a..b", run=world.run,
                                      github=world.github()))


class Main(unittest.TestCase):

    def test_an_empty_range_is_two_not_zero(self):
        repo = Repo(self)
        repo.git(repo.dir, "init", "-q", "-b", "main")
        repo.git(repo.dir, "-c", "user.email=t@example.org", "-c",
                 "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            code = tool.main(["HEAD..HEAD"], run=repo.run(repo.dir),
                             call=World().call, load=lambda **_: project(),
                             environ={})
        self.assertEqual(code, 2)
        self.assertIn("contains no commits", err.getvalue())

    def test_no_configuration_is_two(self):
        def load(**_):
            raise config.ConfigError(["no configuration file"], "/x")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = tool.main(["--pr", "1"], run=World().run,
                             call=World().call, load=load, environ={})
        self.assertEqual(code, 2)
        self.assertIn("no configuration file", err.getvalue())

    def test_it_reads_as_the_token_read_token_gives(self):
        world = World(log="s\n\nplain\x00")
        code, _, _ = world.main(["--pr", "3"], environ={"GH_TOKEN": "t"})
        self.assertEqual(code, 0)
        self.assertEqual(world.tokens, {"t"})

    def test_without_one_it_reads_anonymously(self):
        world = World(log="s\n\nplain\x00")
        world.main(["--pr", "3"], environ={})
        self.assertEqual(world.tokens, {None})


REAL_1253 = ("**This does not close #1159.** That closes once the apply has "
             "run and")


class AFieldThatLagsTheBody(unittest.TestCase):

    LOG = "subject with no keyword\n\nbut fixes #1816 down here\x00"
    SUBJECT = "httpserve: bound the constants (Fixes #1816)\n\nno keyword\x00"

    def run_main(self, body, declared=(), title="t", log=None,
                 argv=("a..b", "--pr", "1819"), the_project=None):
        world = the_branch(log or self.LOG, facts={
            "declared": declared, "title": title, "body": body,
            "commits": 2})
        return world.main(argv, the_project=the_project)

    def test_a_declared_body_with_an_empty_field_says_wait(self):
        code, _, err = self.run_main("Fixes #1816\n")
        self.assertEqual(code, 1)
        self.assertIn("#1816 is declared by a closing keyword in pull "
                      "request 1819's body", err)
        self.assertIn("run the check again", err)
        self.assertNotIn("does not say so", err)

    def test_the_control_a_body_without_the_keyword_keeps_the_old_line(self):
        code, _, err = self.run_main("nothing declared here\n")
        self.assertEqual(code, 1)
        self.assertIn("#1816 will close on merge and the pull request does "
                      "not say so", err)
        self.assertNotIn("rerun", err)

    def test_a_keyword_in_a_code_span_is_not_a_declaration(self):
        _, _, err = self.run_main("quoting `Fixes #1816` only\n")
        self.assertNotIn("rerun", err)
        self.assertIn("does not say so", err)

    def test_a_subject_closer_is_not_sent_to_a_new_branch(self):
        code, _, err = self.run_main(
            "Fixes #1816\n", log=self.SUBJECT,
            argv=("a..b", "--pr", "1819", "--include-subject"))
        self.assertEqual(code, 1)
        self.assertIn("run the check again", err)
        self.assertNotIn("new branch", err)

    def test_a_title_closer_is_not_told_to_retitle(self):
        code, _, err = self.run_main("Fixes #1816\n",
                                     title="t (Fixes #1816)",
                                     log="no keyword\n\nnone\x00")
        self.assertEqual(code, 1)
        self.assertIn("run the check again", err)
        self.assertNotIn("retitle", err)

    def test_once_the_field_settles_it_passes(self):
        code, _, err = self.run_main("Fixes #1816\n", declared=[1816])
        self.assertEqual(code, 0, err)

    def test_where_it_runs_in_ci_it_says_to_rerun_the_job(self):
        rerun = "re-run the failed job (`gh run rerun --failed <run>`)"
        code, _, err = self.run_main("Fixes #1816\n",
                                     the_project=project(rerun=rerun))
        self.assertEqual(code, 1)
        self.assertIn("gh run rerun --failed", err)
        self.assertNotIn("Merger", err)

    def test_field_lags_body_reads_the_body_as_github_does(self):
        self.assertEqual(tool.field_lags_body(
            {"body": "Fixes #1\n`Fixes #2`\ncloses #3", "declared": {3}}),
            {1})


class NegatedClosers(unittest.TestCase):

    def test_the_instance_that_happened(self):
        self.assertEqual(tool.negated_closers(REAL_1253, {1159}),
                         [(1159, "This does not close #1159.")])

    def test_a_negation_in_the_next_sentence_is_not_this_one(self):
        self.assertEqual(tool.negated_closers(
            "Fixes #1251. It does not touch the rest.", {1251}), [])

    def test_a_close_github_will_not_perform_is_not_judged(self):
        self.assertEqual(tool.negated_closers("This does not close #5.",
                                              set()), [])

    QUOTED = ("Fixes #1879\n\nThere is no marker for this, so the check "
              "reports this\ndescription's own `Fixes #1879` as acting.\n")
    UNQUOTED = QUOTED.replace("`", "")

    def test_a_declared_close_quoted_in_a_span_is_not_a_denial(self):
        self.assertEqual(tool.negated_closers(self.QUOTED, {1879}), [])

    def test_the_same_sentence_unquoted_still_refuses(self):
        self.assertEqual(
            tool.negated_closers(self.UNQUOTED, {1879}),
            [(1879, "There is no marker for this, so the check reports "
                    "this description's own Fixes #1879 as acting.")])

    def test_the_sentence_reported_is_the_one_that_was_judged(self):
        body = ("Fixes #1879\n\nThis does not close #1879, whatever "
                "`Fixes #1879` suggests.\n")
        self.assertEqual(
            tool.negated_closers(body, {1879}),
            [(1879, "This does not close #1879, whatever suggests.")])

    def test_a_linked_reference_is_seen(self):
        self.assertEqual(
            tool.negated_closers("It doesn't fix [#7](https://x/7).", {7}),
            [(7, "It doesn't fix [#7](https://x/7).")])

    def test_a_wrapped_bold_sentence_reads_as_one(self):
        self.assertEqual(
            tool.negated_closers("**This will never\nresolve #9.**", {9}),
            [(9, "This will never resolve #9.")])

    def test_a_plain_declaration_passes(self):
        self.assertEqual(tool.negated_closers("Fixes #1258.", {1258}), [])

    def test_neither_is_a_denial(self):
        body = ('"isolated". This change covers the manifest. Neither fixes '
                '#1271, which\nstays open.')
        self.assertEqual(tool.negated_closers(body, {1271}),
                         [(1271, "Neither fixes #1271, which stays open.")])

    def test_nothing_is_a_denial(self):
        self.assertEqual(
            tool.negated_closers("It fixes #1072 and does nothing here.",
                                 {1072}),
            [(1072, "It fixes #1072 and does nothing here.")])


class WillClose(unittest.TestCase):
    def test_at_one_commit_the_commit_subject_counts_and_the_title_does_not(
            self):
        got = tool.will_close({"declared": set(), "title": "Fix #2 here",
                               "commits": 1},
                              ["Fix #1 in the subject\n\nbody"])
        self.assertEqual((got["commit messages"], got["title"]), ({1}, set()))

    def test_at_two_commits_the_title_is_the_subject(self):
        got = tool.will_close({"declared": {5}, "title": "Fix #2 here",
                               "commits": 2},
                              ["second\n\ncloses #3", "Fix #1 first"])
        self.assertEqual(got, {"body": {5}, "title": {2},
                               "commit messages": {1, 3}})

    def test_the_synthetic_merge_commit_is_not_the_one_commit(self):
        merge = "Merge %s into %s" % ("a" * 40, "b" * 40)
        got = tool.will_close({"declared": set(), "title": "Fix #2",
                               "commits": None},
                              [merge, "Fix #1 subject"])
        self.assertEqual((got["commit messages"], got["title"]), ({1}, set()))


class PrFacts(unittest.TestCase):
    def test_title_body_and_count_are_read(self):
        facts = tool.pr_facts(1, answering(200, pr_payload(
            declared=[7], title="T", body="B", commits=3)))
        self.assertEqual(facts, {"declared": {7}, "title": "T", "body": "B",
                                 "commits": 3})

    def test_the_old_shape_still_declares(self):
        payload = {"data": {"repository": {"pullRequest": {
            "closingIssuesReferences": {"nodes": [{"number": 4}]}}}}}
        self.assertEqual(tool.declared(1, answering(200, payload)), {4})

    def test_it_asks_about_the_configured_repository(self):
        seen = []

        def call(url, token, scheme="Bearer", method="GET", body=None):
            seen.append((url, method, body))
            return 200, pr_payload()
        tool.pr_facts(12, tool.GitHub(None, call))
        url, method, body = seen[0]
        self.assertTrue(url.endswith("/graphql"))
        self.assertEqual(method, "POST")
        self.assertEqual(body["variables"],
                         {"o": "example", "r": "widget", "n": 12})


class MainThroughThePullRequest(unittest.TestCase):

    def run_main(self, body, declared, log="subject\n\nplain\x00"):
        world = the_branch(log, facts={"declared": declared, "body": body})
        return world.main(["a..b", "--pr", "1253"])

    def test_a_denied_close_in_the_body_is_one(self):
        code, out, err = self.run_main(REAL_1253, [1159])
        self.assertEqual(code, 1)
        self.assertIn("#1159", err)
        self.assertIn("will close on merge:         #1159", out)

    def test_the_denial_s_remedy_says_how_to_run_it_again(self):
        _, _, err = self.run_main(REAL_1253, [1159])
        self.assertIn("Reword so no closing keyword sits in front of the "
                      "number, then run the check again (the Merger does, "
                      "on its next tick).", err)

    def test_a_meant_close_prints_the_set_and_is_zero(self):
        code, out, _ = self.run_main("Fixes #1251.", [1251])
        self.assertEqual(code, 0)
        self.assertIn("will close on merge:         #1251", out)
        self.assertIn("from the body: #1251", out)

    SPANNED = ("Fixes #1879\n\nThere is no marker for this, so the check "
               "reports this\ndescription's own `Fixes #1879` as acting.\n")

    def test_a_body_quoting_its_own_declaration_exits_zero(self):
        code, out, err = self.run_main(self.SPANNED, [1879])
        self.assertEqual(code, 0)
        self.assertIn("will close on merge:         #1879", out)
        self.assertNotIn("denies", err)

    def test_the_same_body_without_the_backticks_exits_one(self):
        code, _, err = self.run_main(self.SPANNED.replace("`", ""), [1879])
        self.assertEqual(code, 1)
        self.assertIn("#1879", err)


class ASubjectOnlyCloser(unittest.TestCase):

    SUBJECT_ONLY = ("httpserve: bound the constants (Fixes #1816)\n\n"
                    "no keyword here\x00")

    def run_main(self, declared, argv=("a..b", "--pr", "1819"), log=None):
        world = the_branch(log or self.SUBJECT_ONLY, facts={
            "declared": declared, "title": "t (#1816)"})
        return world.main(argv)

    def test_the_two_counts_no_longer_share_a_name(self):
        _, out, _ = self.run_main([])
        self.assertIn("    from the commit messages: #1816", out)
        self.assertIn("0 issue(s) closed by a commit body", out)

    def test_it_is_stated_in_the_default_mode_without_failing(self):
        code, out, err = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn(
            "#1816 will close on merge from a commit subject (c0ffee00), "
            "which the pull request does not declare", out)
        self.assertNotIn("#1816", err)

    def test_it_names_the_commit_the_subject_belongs_to(self):
        _, out, _ = self.run_main(
            [], log=("chore: nothing to see\n\nno keyword here\x00"
                     + self.SUBJECT_ONLY))
        self.assertIn("(c0ffee01)", out)
        self.assertNotIn("(c0ffee00)", out)

    def test_a_declared_subject_closer_says_nothing(self):
        code, out, _ = self.run_main([1816])
        self.assertEqual(code, 0)
        self.assertNotIn("from a commit subject", out)

    def test_include_subject_judges_it_instead_of_stating_it(self):
        code, out, err = self.run_main(
            [], argv=("a..b", "--pr", "1819", "--include-subject"))
        self.assertEqual(code, 1)
        self.assertIn("#1816 will close on merge from c0ffee00's subject, "
                      "which the pull request does not declare", err)
        self.assertNotIn("from a commit subject", out)
        self.assertIn("1 issue(s) closed by a commit message, subject "
                      "included", out)

    def test_the_error_names_both_remedies_and_the_commit(self):
        _, _, err = self.run_main(
            [], argv=("a..b", "--pr", "1819", "--include-subject"))
        self.assertIn("c0ffee00", err)
        self.assertIn("Either declare it", err)
        self.assertIn("rewording that subject on a new branch", err)

    def test_a_body_borne_closer_is_not_offered_the_second_door(self):
        _, _, err = self.run_main(
            [], argv=("a..b", "--pr", "1819"),
            log="subject with no keyword\n\nbut fixes #1816 down here\x00")
        self.assertIn("#1816 will close on merge and the pull request does "
                      "not say so", err)
        self.assertNotIn("new branch", err)


class ADenialInACommitMessage(unittest.TestCase):

    DENIAL = ("render: pin the two tile queue depths\n\n"
              "Referenced rather than closed: #1816 is multi-owner.\x00")

    def run_main(self, log, declared=(), argv=("a..b", "--pr", "1898")):
        return the_branch(log, facts={"declared": declared}).main(argv)

    DENIAL_IN_A_SPAN = (
        "render: pin the two tile queue depths\n\n"
        "Not here: `Closes #1816` would be wrong, it is multi-owner.\x00")

    def test_a_span_does_not_disarm_a_denial_in_a_commit_message(self):
        code, _, err = self.run_main(self.DENIAL_IN_A_SPAN)
        self.assertEqual(code, 1)
        self.assertIn("denies it", err)

    DENIAL_WITH_THE_REFERENCE_SPANNED = (
        "render: pin the two tile queue depths\n\n"
        "Not here: Closes `#1816` would be wrong, it is multi-owner.\x00")

    def test_a_span_around_the_reference_alone_hides_the_pair_entirely(self):
        _, _, spanned_phrase = self.run_main(self.DENIAL_IN_A_SPAN)
        code, _, spanned_ref = self.run_main(
            self.DENIAL_WITH_THE_REFERENCE_SPANNED)
        self.assertIn("denies it", spanned_phrase)
        self.assertNotIn("denies it", spanned_ref)
        self.assertNotIn("1816", spanned_ref)
        self.assertEqual(code, 0)

    def test_the_denial_is_named_as_a_denial(self):
        code, _, err = self.run_main(self.DENIAL)
        self.assertEqual(code, 1)
        self.assertIn("denies it", err)
        self.assertIn("Referenced rather than closed", err)

    def test_it_does_not_tell_the_author_to_declare_it(self):
        _, _, err = self.run_main(self.DENIAL)
        self.assertIn("Do NOT declare it in the body", err)
        self.assertNotIn("the pull request does not say so", err)

    def test_it_names_the_commit_the_sentence_is_in(self):
        _, _, err = self.run_main("chore: nothing\n\nno keyword\x00"
                                  + self.DENIAL)
        self.assertIn("c0ffee01", err)
        self.assertNotIn("c0ffee00", err)

    def test_a_plain_undeclared_closer_keeps_the_old_message(self):
        _, _, err = self.run_main("something\n\nFixes #1234.\x00")
        self.assertIn("the pull request does not say so", err)
        self.assertNotIn("denies it", err)

    def test_a_subject_closer_still_gets_the_subject_door(self):
        _, _, err = self.run_main(
            "pin the depths (Fixes #1816)\n\n"
            "Referenced rather than closed: #1816 is multi-owner.\x00",
            argv=("a..b", "--pr", "1898", "--include-subject"))
        self.assertIn("rewording that subject on a new branch", err)
        self.assertNotIn("denies it", err)

    def test_the_summary_counts_it(self):
        _, out, _ = self.run_main(self.DENIAL)
        self.assertIn("1 closed from a denial", out)

    def test_the_wider_word_list_is_only_for_commit_messages(self):
        self.assertIsNone(tool.NEGATION_RE.search("closed rather than open"))
        self.assertIsNotNone(
            tool.COMMIT_DENIAL_RE.search("closed rather than open"))
        self.assertIsNotNone(tool.NEGATION_RE.search("does not close"))


class ATitleOnlyCloser(unittest.TestCase):

    TWO = ("subject one\n\nno keyword\x00"
           "subject two\n\nno keyword\x00")
    ONE = "subject one\n\nno keyword\x00"

    def run_main(self, log, commits, declared=(), title="x (Fixes #999)"):
        world = the_branch(log, facts={"declared": declared, "title": title,
                                       "commits": commits})
        return world.main(["a..b", "--pr", "1"])

    def test_it_refuses_a_title_closer_the_body_does_not_declare(self):
        code, _, err = self.run_main(self.TWO, commits=2)
        self.assertEqual(code, 1)
        self.assertIn("from the pull request's title", err)
        self.assertIn("Either retitle", err)

    def test_at_one_commit_the_title_is_inert(self):
        code, _, err = self.run_main(self.ONE, commits=1)
        self.assertEqual(code, 0)
        self.assertNotIn("title", err)

    def test_a_declared_title_closer_passes(self):
        code, _, err = self.run_main(self.TWO, commits=2, declared=(999,))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_it_is_counted_as_not_declared(self):
        _, out, _ = self.run_main(self.TWO, commits=2)
        self.assertIn("1 not declared", out)

    def test_the_undercounted_line_no_longer_claims_a_commit_subject(self):
        _, out, _ = self.run_main(self.TWO, commits=2)
        self.assertNotIn("from a commit subject", out)


class IsIssue(unittest.TestCase):

    def test_an_issue_is_true(self):
        self.assertIs(tool.is_issue(1072, answering(200, {"number": 1072})),
                      True)

    def test_a_pull_request_is_false(self):
        self.assertIs(tool.is_issue(1004, answering(
            200, {"number": 1004, "pull_request": {}})), False)

    def test_a_failed_lookup_is_none_not_true(self):
        self.assertIsNone(tool.is_issue(1, answering(404, {})))

    def test_unexpected_output_is_none(self):
        self.assertIsNone(tool.is_issue(1, answering(200, {"message":
                                                           "banana"})))


class SurveyFiltersPullRequests(unittest.TestCase):

    def survey(self, log, kinds):
        world = World(log=log, kinds=kinds, facts=None)
        return tool.survey("a..b", run=world.run, github=world.github())

    def test_a_pull_request_closer_is_dropped(self):
        undeclared, from_commits, _ = self.survey(
            "subject\n\npull_request closed #1004 and the push behind it.\x00",
            {1004: "pr"})
        self.assertEqual(from_commits, set())
        self.assertEqual(undeclared, set())

    def test_an_issue_closer_survives(self):
        _, from_commits, _ = self.survey(
            "subject\n\nfixes #1072 and does nothing here\x00",
            {1072: "issue"})
        self.assertEqual(from_commits, {1072})

    def test_a_mixed_body_keeps_only_the_issue(self):
        _, from_commits, _ = self.survey(
            "subject\n\ncloses #1004 and fixes #1072\x00",
            {1004: "pr", 1072: "issue"})
        self.assertEqual(from_commits, {1072})

    def test_an_unresolvable_number_refuses_rather_than_guessing(self):
        undeclared, unknown, known = self.survey(
            "subject\n\nfixes #999\x00", {})
        self.assertEqual(undeclared, "unknown")
        self.assertEqual(unknown, {999})
        self.assertIsNone(known)


class CommitMessagesAgainstRealGit(unittest.TestCase):

    def setUp(self):
        self.repo = Repo(self)
        self.tree = self.repo.dir
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.org")
        self.git("config", "user.name", "t")
        self.commit("base: the range has to start somewhere")
        self.commit("first: a subject\n\na body mentioning #1 without a "
                    "keyword")
        self.commit("second: another subject (Fixes #2)\n\ncloses #3 down "
                    "here")
        self.run = self.repo.run(self.tree)

    def git(self, *args):
        return self.repo.git(self.tree, *args)

    def head(self):
        return self.git("rev-parse", "--short", "HEAD").stdout.strip()

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def test_each_commit_comes_back_as_its_sha_and_its_whole_message(self):
        got = tool.commit_messages("HEAD~1..HEAD", cwd=self.tree,
                                   run=self.run)
        self.assertEqual(len(got), 1)
        sha, message = got[0]
        self.assertEqual(sha, self.head())
        self.assertEqual(
            message,
            "second: another subject (Fixes #2)\n\ncloses #3 down here")

    def test_the_subject_survives_the_sha_being_on_the_same_line(self):
        got = dict(tool.commit_messages("HEAD~2..HEAD", cwd=self.tree,
                                        run=self.run))
        subjects = {m.split("\n")[0] for m in got.values()}
        self.assertEqual(subjects, {"first: a subject",
                                    "second: another subject (Fixes #2)"})

    def test_the_subject_closer_is_found_with_its_commit(self):
        got = tool.commit_messages("HEAD~2..HEAD", cwd=self.tree,
                                   run=self.run)
        self.assertEqual(tool.subject_closers(got), {2: [self.head()]})

    def test_an_unreadable_range_is_none_rather_than_empty(self):
        self.assertIsNone(tool.commit_messages("no-such-ref..HEAD",
                                               cwd=self.tree, run=self.run))


class BlankCode(unittest.TestCase):

    def test_an_inline_span_goes(self):
        self.assertEqual(tool.blank_code("a `b` c"), "a     c")

    def test_a_fence_goes_and_its_newlines_stay(self):
        got = tool.blank_code("a\n```\nfixes #7\n```\nb")
        self.assertEqual(got.split("\n"), ["a", "   ", "        ", "   ", "b"])

    def test_every_offset_outside_a_code_region_is_unmoved(self):
        text = "one `two` three\n```\nfour\n```\nfive `six` seven\n"
        got = tool.blank_code(text)
        self.assertEqual(len(got), len(text))
        self.assertEqual([i for i, c in enumerate(got) if c == "\n"],
                         [i for i, c in enumerate(text) if c == "\n"])
        for i, c in enumerate(got):
            if c != " ":
                self.assertEqual(c, text[i], "index %d moved" % i)

    def test_a_double_tick_span_holding_a_tick_goes_whole(self):
        self.assertEqual(tool.blank_code("x ``a `b` c`` y"),
                         "x " + " " * len("``a `b` c``") + " y")

    def test_a_lone_backtick_blanks_nothing(self):
        self.assertEqual(tool.blank_code("fixes #7 `"), "fixes #7 `")

    def test_a_span_does_not_cross_a_blank_line(self):
        text = ("Use `widget check to do the thing.\n\n"
                "Fixes #123\n\n"
                "And the `tool` reads it.\n")
        got = tool.blank_code(text)
        self.assertIn("Fixes #123", got,
                      "the keyword was blanked by a span crossing a blank "
                      "line")
        self.assertNotIn("tool", got, "the real span should still be blanked")

    def test_a_span_may_still_cross_a_single_newline(self):
        text = "see `fixes\n#7` for the wording\n"
        self.assertNotIn("fixes", tool.blank_code(text))

    def test_a_tilde_fence_goes(self):
        got = tool.blank_code("a\n~~~\nfixes #7\n~~~\nb")
        self.assertNotIn("fixes #7", got)
        self.assertEqual(got.split("\n")[0], "a")
        self.assertEqual(got.split("\n")[4], "b")

    def test_an_unbalanced_run_is_not_a_span_and_stays_live(self):
        for text in ("``fixes #7`", "`fixes #7``"):
            self.assertIn("fixes #7", tool.blank_code(text),
                          "%r is not a code span" % text)

    def test_three_backticks_at_a_line_start_are_a_fence_not_a_span(self):
        self.assertNotIn("fixes #7", tool.blank_code("``` fixes #7 ``"))


class UnsafePairs(unittest.TestCase):
    def test_a_body_is_disarmed_by_a_code_span_and_a_commit_is_not(self):
        text = "I withdrew `tools: thing (Fixes #1825)` earlier.\n"
        self.assertEqual(tool.unsafe_pairs(text, "body"), [])
        self.assertEqual([(n, m) for _l, _c, n, m
                          in tool.unsafe_pairs(text, "commit")],
                         [(1825, "Fixes #1825")])

    def test_a_newline_between_the_keyword_and_the_reference_still_acts(self):
        text = ("tools: keep the tests from a withdrawn fix (for #1825)\n\n"
                "so merging #1832 would have closed\n"
                "#1825, on the strength of the claim withdrawn above.\n")
        got = tool.unsafe_pairs(text, "commit")
        self.assertEqual([(n, m) for _l, _c, n, m in got],
                         [(1825, "closed\n#1825")])
        self.assertEqual(got[0][0], 3, "line should be where the keyword is")

    def test_the_reported_position_is_the_keyword_not_the_reference(self):
        text = "one\ntwo\nthis fixes #7 here\n"
        [(line, column, number, _m)] = tool.unsafe_pairs(text, "commit")
        self.assertEqual((line, number), (3, 7))
        self.assertEqual(text.split("\n")[2][column - 1:column + 4], "fixes")

    def test_positions_survive_a_span_earlier_on_the_same_line(self):
        text = "see `fixes #1` then fixes #2\n"
        [(line, column, number, _m)] = tool.unsafe_pairs(text, "body")
        self.assertEqual((line, number), (1, 2))
        self.assertEqual(text[column - 1:column + 4], "fixes")

    def test_each_pair_is_reported_in_order(self):
        text = "fixes #1\nsee #2\nresolves #3\n"
        self.assertEqual([n for _l, _c, n, _m
                          in tool.unsafe_pairs(text, "commit")], [1, 3])

    def test_a_note_may_quote_its_own_failed_sentence_in_a_body(self):
        text = ("Do not let this close anything. My first attempt said\n"
                "`This does not close #1159.` and the denial matched.\n")
        self.assertEqual(tool.unsafe_pairs(text, "body"), [])


class TextFile:
    """check_body over a text in a file of its own."""

    def write(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "text.md")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def run_on(self, text, surface="body", declares=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tool.check_body(self.write(text), surface, declares=declares)
        return rc, out.getvalue()


class CheckBody(TextFile, unittest.TestCase):

    def test_a_clean_text_exits_zero(self):
        rc, out = self.run_on("nothing to see, see #7\n", "body")
        self.assertEqual(rc, 0)
        self.assertIn("no closing keyword", out)

    def test_an_armed_text_exits_one_and_names_the_position(self):
        rc, out = self.run_on("a\nb fixes #7\n", "commit")
        self.assertEqual(rc, 1)
        self.assertIn(":2:3:", out)
        self.assertIn("#7", out)

    def test_an_unreadable_file_exits_two_rather_than_zero(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = tool.check_body("/nonexistent/nothing.md", "body")
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err.getvalue())

    def test_a_commit_subject_keyword_is_reported_and_does_not_fail(self):
        rc, out = self.run_on("tools: a thing (Fixes #1879)\n\nbody\n",
                              "commit")
        self.assertEqual(rc, 0)
        self.assertIn("deliberate", out)
        self.assertIn("cannot be reworded", out)

    def test_a_subject_notice_does_not_hide_a_live_body_keyword(self):
        rc, out = self.run_on("tools: thing (Fixes #1)\n\n"
                              "and this fixes #2 by accident\n", "commit")
        self.assertEqual(rc, 1)
        self.assertIn("#1 from the subject line", out)
        self.assertIn("would close #2", out)

    def test_a_body_surface_has_no_subject_exemption(self):
        rc, _out = self.run_on("Fixes #1\n", "body")
        self.assertEqual(rc, 1)

    def test_the_remedy_printed_differs_by_surface(self):
        _rc, body = self.run_on("fixes #7\n", "body")
        _rc, commit = self.run_on("subject\n\nfixes #7\n", "commit")
        self.assertIn("code span", body)
        self.assertIn("force-push", commit)
        self.assertNotIn("put the keyword inside a code span", commit)


class TheCommitSurfaceSaysWhichFindingsAreEstablished(TextFile,
                                                      unittest.TestCase):

    SPANNED_AND_PLAIN = ("subject line with no keyword\n\n"
                         "A spanned one: `Fixes #1825` is quoted here.\n\n"
                         "An unspanned one: Fixes #1825 plainly.\n")

    def run_on(self, text, surface="commit", declares=None):
        return super().run_on(text, surface, declares)

    @staticmethod
    def notes(out):
        return [line for line in out.splitlines()
                if line.startswith("    inside a code region:")]

    def test_the_two_findings_no_longer_read_identically(self):
        _rc, out = self.run_on(self.SPANNED_AND_PLAIN)
        lines = out.splitlines()
        findings = [i for i, line in enumerate(lines)
                    if "would close #1825" in line]
        self.assertEqual(len(findings), 2)
        self.assertEqual(lines[findings[0]].split(": ", 1)[1],
                         lines[findings[1]].split(": ", 1)[1],
                         "the finding lines themselves should still match")
        self.assertTrue(lines[findings[0] + 1].startswith(
            "    inside a code region:"), lines[findings[0] + 1])
        self.assertFalse(lines[findings[1] + 1].startswith(
            "    inside a code region:"), lines[findings[1] + 1])

    def test_the_verdict_is_unchanged_and_both_are_counted(self):
        rc, out = self.run_on(self.SPANNED_AND_PLAIN)
        self.assertEqual(rc, 1)
        self.assertIn("2 keyword(s)", out)

    def test_the_tally_says_how_many_are_unmeasured(self):
        _rc, out = self.run_on(self.SPANNED_AND_PLAIN)
        self.assertIn("1 of those is inside a code region", out)
        self.assertIn("not because it was measured.\n", out)

    def test_the_tally_counts_only_the_number_it_qualifies(self):
        rc, out = self.run_on("fix: `Fixes #7` in the subject\n\n"
                              "and Fixes #8 plainly\n")
        self.assertEqual(rc, 1)
        self.assertIn("1 keyword(s)", out)
        self.assertNotIn("of those", out)
        self.assertEqual(len(self.notes(out)), 1)

    def test_the_tally_line_is_absent_when_nothing_is_quoted(self):
        _rc, out = self.run_on("subject\n\nFixes #7 plainly\n")
        self.assertIn("1 keyword(s)", out)
        self.assertNotIn("of those", out)

    def test_a_plain_run_says_nothing_about_spans(self):
        _rc, out = self.run_on("subject\n\nFixes #7 plainly\n")
        self.assertIn("would close #7", out)
        self.assertEqual(self.notes(out), [])

    def test_a_fenced_keyword_is_noted_too(self):
        _rc, out = self.run_on("subject\n\n```\nFixes #7\n```\n")
        self.assertIn("would close #7", out)
        self.assertEqual(len(self.notes(out)), 1)

    def test_the_subject_notice_carries_it_as_well(self):
        rc, out = self.run_on("fix: `Fixes #7` in the subject\n\nbody\n")
        self.assertEqual(rc, 0)
        self.assertIn("from the subject line", out)
        self.assertEqual(len(self.notes(out)), 1)

    def test_the_body_surface_is_untouched(self):
        rc, out = self.run_on(self.SPANNED_AND_PLAIN, "body")
        self.assertEqual(rc, 1)
        self.assertIn("1 keyword(s)", out)
        self.assertEqual(self.notes(out), [])

    def test_quoted_pairs_is_empty_where_nothing_is_quoted(self):
        self.assertEqual(tool.quoted_pairs("subject\n\nFixes #7\n"), set())
        self.assertEqual(tool.quoted_pairs("subject\n\n`Fixes #7`\n"),
                         {(3, 2)})


class CheckBodyComparesAgainstADeclaration(TextFile, unittest.TestCase):

    DECLARED = "This branch fixes the thing.\n\nCloses #1948.\n"
    TWO = "Closes #1948 and closes #1999.\n"

    def test_row_one_a_declared_close_is_not_a_finding(self):
        rc, out = self.run_on(self.DECLARED, declares=[1948])
        self.assertEqual(rc, 0)
        self.assertIn("as declared", out)

    def test_row_two_only_the_undeclared_one_is_the_finding(self):
        rc, out = self.run_on(self.TWO, declares=[1948])
        self.assertEqual(rc, 1)
        self.assertIn("'closes #1999' would close #1999 on merge", out)
        self.assertNotIn("'Closes #1948' would close #1948 on merge", out)
        self.assertIn("1 keyword(s)", out)

    def test_row_two_names_the_flag_that_would_satisfy_it(self):
        _rc, out = self.run_on(self.TWO, declares=[1948])
        self.assertIn("`--declares 1999`", out)

    def test_row_three_a_declaration_nothing_closes_is_a_notice(self):
        rc, out = self.run_on(self.DECLARED, declares=[1948, 1999])
        self.assertEqual(rc, 0)
        self.assertIn("#1999 is declared and no keyword here closes it", out)

    def test_row_three_says_why_it_is_not_failing(self):
        _rc, out = self.run_on(self.DECLARED, declares=[1948, 1999])
        self.assertIn("cannot see one", out)

    def test_row_four_no_keyword_reads_differently_from_all_declared(self):
        empty_rc, empty = self.run_on("nothing here\n", declares=[])
        full_rc, full = self.run_on(self.DECLARED, declares=[1948])
        self.assertEqual((empty_rc, full_rc), (0, 0))
        self.assertIn("carries no closing keyword", empty)
        self.assertIn("every one is declared", full)
        self.assertNotEqual(empty, full)

    def test_the_direction_of_the_difference(self):
        undeclared_rc, _out = self.run_on(self.TWO, declares=[1948])
        unclosed_rc, _out = self.run_on(self.DECLARED, declares=[1948, 1999])
        self.assertEqual((undeclared_rc, unclosed_rc), (1, 0))

    def test_without_the_flag_nothing_changes(self):
        rc, out = self.run_on(self.DECLARED)
        self.assertEqual(rc, 1)
        self.assertIn("would close #1948 on merge", out)
        self.assertIn("nothing was declared against", out)

    def test_an_empty_declaration_is_not_the_absent_one(self):
        empty, empty_text = self.run_on(self.DECLARED, declares=[])
        absent, absent_text = self.run_on(self.DECLARED)
        self.assertEqual((empty, absent), (1, 1))
        self.assertIn("were not declared", empty_text)
        self.assertIn("nothing was declared against", absent_text)

    def test_a_commit_subject_is_still_exempt(self):
        rc, out = self.run_on("subject fixes #7\n", "commit", declares=[])
        self.assertEqual(rc, 0)
        self.assertIn("cannot be reworded", out)


class CheckBodyRefusesADenial(TextFile, unittest.TestCase):

    RUN_ON = ("Fixes #1862: the annotation printed a bare wake, with "
              "nothing to say a wake wasn't actually owed.\n")
    SPLIT = ("Fixes #1862.\n\nThe annotation printed a bare wake, with "
             "nothing to say a wake wasn't actually owed.\n")

    def test_a_declared_close_in_a_denying_sentence_is_refused(self):
        rc, out = self.run_on(self.RUN_ON, declares=[1862])
        self.assertEqual(rc, 1)
        self.assertIn("#1862 would close on merge from a sentence that "
                      "denies it", out)
        self.assertIn("wasn't actually owed", out,
                      "the sentence judged is shown, so the author can "
                      "see which words did it")

    def test_the_same_words_with_the_keyword_on_its_own_pass(self):
        rc, out = self.run_on(self.SPLIT, declares=[1862])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("denies it", out)

    def test_an_outright_denial_is_refused_and_both_remedies_named(self):
        rc, out = self.run_on("This does not fix #1862.\n")
        self.assertEqual(rc, 1)
        self.assertIn("denies it", out)
        self.assertIn("If the close is meant", out)
        self.assertIn("If it is not, take the keyword off the number", out)

    def test_it_names_what_refuses_it(self):
        _rc, out = self.run_on("This does not fix #1862.\n")
        self.assertIn("  The Merger refuses this body as written.", out)
        tool.configure(project(enforced_by="`pull-request-checks`"))
        self.addCleanup(tool.configure, project())
        _rc, out = self.run_on("This does not fix #1862.\n")
        self.assertIn("  `pull-request-checks` refuses this body", out)

    def test_a_denial_quoted_in_a_code_span_is_not_judged(self):
        rc, out = self.run_on("Fixes #1862.\n\nIt does not say "
                              "`does not fix #1862` anywhere.\n",
                              declares=[1862])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("denies it", out)

    def test_the_commit_surface_is_not_judged_for_a_denial(self):
        rc, out = self.run_on("A subject line\n\n" + self.RUN_ON,
                              surface="commit", declares=[1862])
        self.assertNotIn("denies it", out)
        self.assertEqual(rc, 0, out)


class CheckBodyCommandLine(TextFile, unittest.TestCase):

    def main(self, argv, run=None, call=None):
        def explode(*args, **_):
            raise AssertionError("check-body ran %r" % (args,))
        return tool.main(argv, run=run or explode, call=call or explode,
                         load=lambda **_: project(), environ={})

    def test_the_surface_is_required(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.main(["--check-body", "/dev/null"])
        self.assertIn("--surface", err.getvalue())

    def test_a_surface_without_a_body_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.main(["--surface", "body"])
        self.assertIn("only means anything", err.getvalue())

    def test_check_body_asks_git_and_github_for_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.main(["--check-body", "/dev/null", "--surface", "body"])
        self.assertEqual(rc, 0)

    def test_declares_reaches_check_body(self):
        path = self.write("Closes #1948.\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.main(["--check-body", path, "--surface", "body",
                            "--declares", "1948"])
        self.assertEqual(rc, 0)
        self.assertIn("as declared", out.getvalue())

    def test_declares_without_a_body_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.main(["a..b", "--declares", "1948"])
        self.assertIn("--pr N", err.getvalue())


class CheckBodyAgainstTheRealCommandLine(TextFile, unittest.TestCase):

    def run_tool(self, text, surface):
        env = dict(os.environ, FORGE_CONFIG=str(EXAMPLE))
        return subprocess.run([str(TOOL), "--check-body", self.write(text),
                               "--surface", surface],
                              capture_output=True, text=True, env=env)

    def test_clean_is_zero_and_armed_is_one(self):
        self.assertEqual(self.run_tool("see #7\n", "body").returncode, 0)
        self.assertEqual(self.run_tool("fixes #7\n", "body").returncode, 1)

    def test_the_span_that_disarms_a_body_does_not_disarm_a_commit(self):
        text = "subject\n\nquoting `fixes #7` here\n"
        self.assertEqual(self.run_tool(text, "body").returncode, 0)
        self.assertEqual(self.run_tool(text, "commit").returncode, 1)


class PrCommits(unittest.TestCase):

    def commits(self, n):
        return [{"sha": "%040x" % i, "commit": {"message": "s%d\n\nbody\n" % i}}
                for i in range(n)]

    def paging(self, items):
        seen = []

        def call(url, token, scheme="Bearer", method="GET", body=None):
            seen.append(url)
            page = int(url.rsplit("page=", 1)[1])
            return 200, items[(page - 1) * 100:page * 100]
        return seen, tool.GitHub(None, call)

    def test_it_reads_sha_and_message(self):
        _seen, github = self.paging([{"sha": "a" * 40, "commit": {
            "message": "subject\n\nbody\n"}}])
        self.assertEqual(tool.pr_commits(1942, github),
                         [("a" * 40, "subject\n\nbody")])

    def test_two_pages_are_one_list(self):
        seen, github = self.paging(self.commits(101))
        got = tool.pr_commits(1942, github)
        self.assertEqual(len(got), 101)
        self.assertEqual(got[100], ("%040x" % 100, "s100\n\nbody"))
        self.assertEqual(len(seen), 2)

    def test_it_asks_for_a_hundred_a_page(self):
        seen, github = self.paging(self.commits(1))
        tool.pr_commits(1942, github)
        self.assertIn("/repos/example/widget/pulls/1942/commits?per_page=100"
                      "&page=1", seen[0])

    def test_a_failed_call_is_none(self):
        self.assertIsNone(tool.pr_commits(1942, answering(502, {})))

    def test_a_failed_second_page_is_none_not_the_first(self):
        def call(url, token, scheme="Bearer", method="GET", body=None):
            if url.endswith("page=1"):
                return 200, self.commits(100)
            return 502, {}
        self.assertIsNone(tool.pr_commits(1942, tool.GitHub(None, call)))

    def test_a_shape_it_does_not_recognize_is_none(self):
        self.assertIsNone(tool.pr_commits(1942, answering(
            200, [{"sha": "a" * 40}])))

    def test_an_answer_that_is_not_a_list_is_none(self):
        self.assertIsNone(tool.pr_commits(1942, answering(
            200, {"message": "rate limited"})))


class TheCommitsComeFromThePullRequest(unittest.TestCase):

    LOG = "doc-verify: exit 2 (Fixes #1860)\n\nno keyword\x00"

    def world(self, pr_log=None, declared=(1860,)):
        return World(log="unrelated branch\n\nnothing\x00",
                     pr_log=pr_log or self.LOG,
                     facts={"declared": declared})

    def test_the_local_range_is_never_read(self):
        world = self.world()
        world.main(["--pr", "1972"])
        self.assertEqual([a for a in world.seen_git if "log" in a], [])

    def test_the_pull_requests_commit_is_the_one_counted(self):
        code, out, _err = self.world().main(["--pr", "1972",
                                             "--include-subject"])
        self.assertEqual(code, 0)
        self.assertIn("1 issue(s) closed by a commit message", out)
        self.assertIn("from the commit messages: #1860", out)

    def test_the_header_says_where_the_commits_came_from(self):
        _code, out, _err = self.world().main(["--pr", "1972"])
        self.assertIn("1 commit(s) examined, from pull request 1972's "
                      "commits", out)

    def test_commits_it_could_not_fetch_are_two(self):
        world = World(log=None, pr_log=None, facts=None)
        code, _, _ = world.main(["--pr", "1972"])
        self.assertEqual(code, 2)

    def test_an_empty_commit_list_is_two_and_not_a_clean_run(self):
        world = World(log=None, pr_log="", facts=None)
        code, _, err = world.main(["--pr", "1972"])
        self.assertEqual(code, 2)
        self.assertIn("could not read pull request 1972's commits", err)


class ALowerLayersCommits(unittest.TestCase):
    """A restacked layer carries the merged layer below's commits, whose
    keywords that layer's merge already closed (agent-forge #92)."""

    LOG = ("lower: the bars (Closes #4466)\n\nbody\x00"
           "upper: the panels\n\nnothing\x00")

    def world(self, merged=True, shas=None):
        return World(log=self.LOG, facts={"declared": ()}, below={
            4474: (merged, full_shas(self.LOG)[:1] if shas is None
                   else shas)})

    def test_without_below_the_lower_keyword_is_a_finding(self):
        code, _out, err = self.world().main(["--pr", "4479",
                                             "--include-subject"])
        self.assertEqual(code, 1)
        self.assertIn("#4466", err)

    def test_below_leaves_the_lower_layers_commits_out(self):
        code, out, _err = self.world().main(
            ["--pr", "4479", "--include-subject", "--below", "4474"])
        self.assertEqual(code, 0)
        self.assertIn("1 commit(s) examined, from pull request 4479's "
                      "commits, less 1 of merged #4474's", out)

    def test_an_empty_below_is_no_below(self):
        code, _out, _err = self.world().main(
            ["--pr", "4479", "--include-subject", "--below"])
        self.assertEqual(code, 1)

    def test_a_layer_that_has_not_merged_is_two(self):
        code, _out, err = self.world(merged=False).main(
            ["--pr", "4479", "--below", "4474"])
        self.assertEqual(code, 2)
        self.assertIn("pull request 4474 has not merged", err)

    def test_commits_it_could_not_read_are_two(self):
        code, _out, err = self.world(shas=[]).main(
            ["--pr", "4479", "--below", "4474"])
        self.assertEqual(code, 2)
        self.assertIn("could not read pull request 4474's commits", err)

    def test_below_needs_a_pull_request(self):
        with self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            self.world().main(["a..b", "--below", "4474"])


class NotReachableFrom(unittest.TestCase):

    def test_it_asks_git_for_the_range_excluding_the_base(self):
        seen = []

        def run(args, **_):
            seen.append(args)
            return done(args, 0, "")
        tool.not_reachable_from("a...b", "origin/main", run=run)
        self.assertIn("--not", seen[0])
        self.assertIn("origin/main", seen[0])
        self.assertIn("a...b", seen[0])

    def test_the_shas_it_returns(self):
        def run(args, **_):
            return done(args, 0, "d" * 40 + "\n" + "e" * 40 + "\n")
        self.assertEqual(
            tool.not_reachable_from("a..b", "origin/main", run=run),
            {"d" * 40, "e" * 40})

    def test_an_unreadable_base_is_none_rather_than_empty(self):
        self.assertIsNone(tool.not_reachable_from(
            "a..b", "origin/nope", run=lambda args, **_: done(args, 128)))


class PrBaseRef(unittest.TestCase):

    def test_the_name(self):
        self.assertEqual(tool.pr_base_ref(2059, answering(
            200, {"base": {"ref": "main"}})), "main")

    def test_it_reads_base_ref_and_not_base_sha(self):
        github = answering(200, {"base": {"ref": "main", "sha": "f" * 40}})
        self.assertEqual(tool.pr_base_ref(2059, github), "main")
        self.assertEqual(github.seen, ["repos/example/widget/pulls/2059"])

    def test_a_failed_read_is_none(self):
        self.assertIsNone(tool.pr_base_ref(2059, answering(502, {})))

    def test_an_empty_answer_is_none_rather_than_a_ref_named_nothing(self):
        self.assertIsNone(tool.pr_base_ref(2059, answering(
            200, {"base": {"ref": ""}})))


class ARangeThatIsNotThePullRequestsBranch(unittest.TestCase):

    PR_LOG = "subject one (Fixes #1860)\n\nno keyword\x00"
    MINE = full_shas(PR_LOG)[0]
    BOTH = MINE + "\n" + "d" * 40 + "\n"

    def run_main(self, range_out, fresh=None, base="main",
                 argv=("a..b", "--pr", "1972")):
        world = World(log=self.PR_LOG, range_out=range_out, fresh=fresh,
                      base=base, facts={"declared": [1860]})
        return world.main(argv)

    def test_a_range_without_them_is_refused(self):
        code, _out, err = self.run_main("d" * 40 + "\n")
        self.assertEqual(code, 2)
        self.assertIn("is not that pull request's branch", err)

    def test_the_refusal_names_the_missing_commit(self):
        code, _out, err = self.run_main("d" * 40 + "\n")
        self.assertEqual(code, 2)
        self.assertIn(self.MINE[:8], err)

    def test_the_refusal_names_the_command_that_works(self):
        _code, _out, err = self.run_main("d" * 40 + "\n")
        self.assertIn("`--pr 1972` alone", err)

    def test_a_range_that_carries_more_is_judged(self):
        code, out, _err = self.run_main(self.BOTH)
        self.assertEqual(code, 0)
        self.assertIn("1 commit(s) examined, from a..b", out)

    def test_a_range_reaching_back_past_the_base_is_refused(self):
        code, _out, err = self.run_main(self.BOTH, fresh=self.MINE + "\n")
        self.assertEqual(code, 2)
        self.assertIn("already on origin/main", err)

    def test_the_backward_refusal_names_the_commit_it_will_not_judge(self):
        _code, _out, err = self.run_main(self.BOTH, fresh=self.MINE + "\n")
        self.assertIn("d" * 8, err)
        self.assertIn("1 commit(s)", err)

    def test_the_backward_refusal_names_the_dots(self):
        _code, _out, err = self.run_main(self.BOTH, fresh=self.MINE + "\n")
        self.assertIn("Two dots, not three", err)
        self.assertIn("`--pr 1972` alone", err)

    def test_the_two_supersets_differ_only_in_the_base(self):
        ahead, _o, _e = self.run_main(self.BOTH)
        behind, _o2, _e2 = self.run_main(self.BOTH, fresh=self.MINE + "\n")
        self.assertEqual((ahead, behind), (0, 2))

    def test_the_header_names_the_base_it_measured_against(self):
        _code, out, _err = self.run_main(self.BOTH)
        self.assertIn("against base origin/main", out)

    def test_a_base_ref_that_is_not_local_is_two_and_says_to_fetch(self):
        code, _out, err = self.run_main(self.MINE + "\n", fresh=FAIL)
        self.assertEqual(code, 2)
        self.assertIn("cannot read origin/main", err)
        self.assertIn("git fetch origin main", err)

    def test_a_commit_of_its_own_behind_the_base_is_not_a_refusal(self):
        code, out, _err = self.run_main(self.BOTH, fresh="d" * 40 + "\n")
        self.assertEqual(code, 0)
        self.assertIn("1 commit(s) examined", out)

    def test_a_base_that_cannot_be_read_is_two(self):
        code, _out, err = self.run_main(self.MINE + "\n", base=None)
        self.assertEqual(code, 2)
        self.assertIn("base branch", err)

    def test_an_unreadable_range_is_two(self):
        code, _out, err = self.run_main(FAIL)
        self.assertEqual(code, 2)
        self.assertIn("cannot read a..b", err)

    def test_an_empty_range_is_not_that_branch(self):
        code, _out, err = self.run_main("")
        self.assertEqual(code, 2)
        self.assertIn("is not that pull request's branch", err)


class WithoutAPullRequestNothingClaimsOne(unittest.TestCase):

    LOG = "guides/log-events: context (Closes #1855)\n\nno keyword\x00"

    def run_main(self, argv):
        return World(log=self.LOG, branch="some-branch").main(argv)

    def test_it_does_not_say_the_pull_request_declares_nothing(self):
        _code, _out, err = self.run_main(["a..b", "--include-subject"])
        self.assertNotIn("does not declare", err)
        self.assertNotIn("does not say so", err)

    def test_it_does_not_ask_for_a_new_branch(self):
        _code, _out, err = self.run_main(["a..b", "--include-subject"])
        self.assertNotIn("new branch", err)

    def test_it_says_what_was_not_compared(self):
        _code, _out, err = self.run_main(["a..b", "--include-subject"])
        self.assertIn("No pull request was named", err)
        self.assertIn("--pr N", err)

    def test_the_keyword_is_still_reported_and_located(self):
        code, _out, err = self.run_main(["a..b", "--include-subject"])
        self.assertEqual(code, 1)
        self.assertIn("#1855 will close on merge", err)
        self.assertIn("c0ffee00", err)


class TheBareSummaryNamesOnlyWhatItRead(unittest.TestCase):

    LOG = "subject\n\nplain text, no keyword\x00"
    DENIAL = ("render: pin the queue depths\n\n"
              "Referenced rather than closed: #1816 is multi-owner.\x00")

    def summary(self, argv, log=None):
        world = World(log=log or self.LOG, branch="some-branch",
                      facts={"declared": []})
        _code, out, _err = world.main(argv)
        lines = [ln for ln in out.splitlines()
                 if ln.startswith("forge-closing-keywords:")]
        return lines[0] if lines else ""

    def test_naming_a_pull_request_changes_the_summary(self):
        self.assertNotEqual(
            self.summary(["a..b", "--include-subject"]),
            self.summary(["a..b", "--pr", "1", "--include-subject"]))

    def test_the_bare_run_does_not_print_not_declared(self):
        self.assertNotIn("not declared",
                         self.summary(["a..b", "--include-subject"]))

    def test_the_bare_run_says_what_it_did_not_read(self):
        line = self.summary(["a..b", "--include-subject"])
        self.assertIn("No pull request was named", line)
        self.assertIn("the body was not read", line)

    def test_a_commit_denial_still_counts_with_its_scope_named(self):
        line = self.summary(["a..b"], log=self.DENIAL)
        self.assertIn("1 of them from a denial in a commit message", line)

    def test_naming_one_restores_the_three_counters(self):
        line = self.summary(["a..b", "--pr", "1", "--include-subject"])
        self.assertIn("not declared", line)
        self.assertIn("closed from a denial", line)


class IncludeSubjectNeedsThePullRequest(unittest.TestCase):

    LOG = "subject (Closes #1855)\n\nno keyword\x00"

    def run_main(self, branch="mine", open_pr=7, argv=None):
        world = World(log=self.LOG, branch=branch, open_pr=open_pr)
        code, out, err = world.main(argv or ["a..b", "--include-subject"])
        return code, out, err, world

    def test_a_branch_with_an_open_pull_request_is_refused(self):
        code, _out, err, _world = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("#7", err)
        self.assertIn("--pr 7", err)

    def test_a_branch_without_one_is_not_refused(self):
        code, _out, _err, _world = self.run_main(open_pr=0)
        self.assertEqual(code, 1)

    def test_github_being_unreachable_does_not_refuse(self):
        code, _out, _err, _world = self.run_main(open_pr=None)
        self.assertEqual(code, 1)

    def test_a_detached_head_is_not_looked_up(self):
        _code, _out, _err, world = self.run_main(branch="HEAD")
        self.assertEqual([p for p in world.seen_api
                          if "pulls?state=open" in p], [])

    def test_the_default_mode_is_not_gated(self):
        _code, _out, _err, world = self.run_main(argv=["a..b"])
        self.assertEqual([a for a in world.seen_git if "rev-parse" in a], [])

    def test_naming_it_is_not_gated(self):
        _code, _out, _err, world = self.run_main(
            argv=["a..b", "--pr", "7", "--include-subject"])
        self.assertEqual([a for a in world.seen_git if "rev-parse" in a], [])


class OpenPrForHead(unittest.TestCase):

    def test_it_asks_about_the_checked_out_branch(self):
        world = World(branch="fix/1942-pr-commits-reconciled", open_pr=7)
        self.assertEqual(tool.open_pr_for_head(world.github(),
                                               run=world.run), 7)
        self.assertIn("head=example:fix/1942-pr-commits-reconciled",
                      world.seen_api[-1])

    def test_a_branch_name_is_quoted_into_the_query(self):
        world = World(branch="odd&name#1", open_pr=7)
        tool.open_pr_for_head(world.github(), run=world.run)
        self.assertIn("head=example:odd%26name%231", world.seen_api[-1])

    def test_no_pull_request_is_none(self):
        world = World(branch="mine", open_pr=0)
        self.assertIsNone(tool.open_pr_for_head(world.github(),
                                                run=world.run))

    def test_an_unreadable_branch_is_none(self):
        world = World(branch=None, open_pr=7)
        self.assertIsNone(tool.open_pr_for_head(world.github(),
                                                run=world.run))
        self.assertEqual(world.seen_api, [])


class RangeShas(unittest.TestCase):
    def test_it_reads_full_shas(self):
        def run(args, **_):
            return done(args, 0, "a" * 40 + "\n" + "b" * 40 + "\n")
        self.assertEqual(tool.range_shas("a..b", run=run),
                         {"a" * 40, "b" * 40})

    def test_an_unreadable_range_is_none(self):
        self.assertIsNone(tool.range_shas(
            "a..b", run=lambda args, **_: done(args, 1)))


class TheMergeGate(unittest.TestCase):
    """What the Merger asks: the gate as the example configuration
    writes it, run as the Merger runs it. Pinned by outcome, not by the
    flags appearing: on a pull request whose only closer is a commit
    subject, the gate's arguments must refuse, where the tool's default
    passes the same pull request. And since the Merger runs its gates
    from a checkout of the default branch, nothing the gate needs may be
    local: every git read fails here, and the verdict still comes."""

    SUBJECT_ONLY = "httpserve: bound the timeouts (Fixes #1816)\n\nno keyword\x00"

    def gate(self):
        found = [argv for argv in example()["merge"]["gate"]
                 if argv[0] == "forge-closing-keywords"]
        self.assertEqual(len(found), 1, "the example's gate does not run "
                                        "forge-closing-keywords once")
        return [a.replace("{pr}", "1819") for a in found[0][1:]]

    def run_main(self, argv, declared=(), body=""):
        world = World(log=None, pr_log=self.SUBJECT_ONLY, range_out=FAIL,
                      fresh=FAIL, branch=None,
                      facts={"declared": declared, "body": body})
        return world.main(argv)

    def test_it_names_the_pull_request_and_no_range(self):
        argv = self.gate()
        self.assertIn("--pr", argv)
        self.assertEqual([a for a in argv if ".." in a], [])

    def test_it_refuses_a_closer_only_a_subject_carries(self):
        code, out, err = self.run_main(self.gate())
        self.assertEqual(code, 1, "the gate's arguments let a subject-borne "
                                  "undeclared closer through")
        self.assertIn("#1816", err)
        self.assertIn("will close on merge:         #1816", out)

    def test_the_default_form_passes_the_same_pull_request(self):
        code, out, _ = self.run_main(["--pr", "1819"])
        self.assertEqual(code, 0)
        self.assertIn("will close on merge:         #1816", out)

    def test_it_does_not_refuse_a_subject_the_body_declares(self):
        for argv in (["--pr", "1819"], self.gate()):
            with self.subTest(argv=argv):
                code, _, err = self.run_main(argv, declared=(1816,),
                                             body="Fixes #1816.")
                self.assertEqual(code, 0, err)

    def test_a_range_alongside_needs_local_git_and_is_refused(self):
        code, _, _ = self.run_main(["origin/main..HEAD", "--pr", "1819",
                                    "--include-subject"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
