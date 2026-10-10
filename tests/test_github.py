"""Tests for agent_forge.github. GitHub is faked through `call`; signing
uses a key generated for the test, never one kept in the tree."""
import base64
import json
import os
import subprocess
import tempfile
import unittest

from agent_forge import config, github
from tests.helpers import example


def fake_sign(key_path, message):
    return b"sig-of-" + key_path.encode()


class FakeGitHub:
    def __init__(self, slug="leaddevwidgetagent", app=200, tokens=201,
                 installation=200):
        self.slug, self.app, self.tokens, self.installation = \
            slug, app, tokens, installation
        self.calls = []

    def __call__(self, url, token, scheme="Bearer", method="GET", body=None):
        self.calls.append((method, url, scheme, body))
        if url.endswith("/app"):
            return self.app, ({"slug": self.slug} if self.app == 200 else
                              {"message": "no"})
        if url.endswith("/access_tokens"):
            return self.tokens, ({"token": "ghs_x", "expires_at": "later"}
                                 if self.tokens == 201 else {})
        if "/app/installations/" in url:
            return self.installation, {"account": {"login": "example"}}
        return 200, {"url": url}


class Case(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = example()
        data["key_dir"] = self.tmp.name
        self.project = config.Project(data, "/p/.agent-forge/config.json")
        for name in ("LeadDev", "Helper"):
            with open(self.project.key_path(name), "w") as handle:
                handle.write("not a key\n")


class Jwt(unittest.TestCase):

    def test_a_real_rs256_assertion_that_openssl_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = os.path.join(tmp, "k.pem")
            pub = os.path.join(tmp, "k.pub")
            subprocess.run(["openssl", "genrsa", "-out", key, "2048"],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "rsa", "-in", key, "-pubout", "-out",
                            pub], check=True, capture_output=True)
            jwt = github.make_jwt(42, key, now=1_000_000)
            head, claims, sig = jwt.split(".")

            def decode(part):
                return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
            self.assertEqual(json.loads(decode(head)),
                             {"alg": "RS256", "typ": "JWT"})
            self.assertEqual(json.loads(decode(claims)),
                             {"iat": 999_940, "exp": 1_000_540, "iss": "42"})
            sigfile = os.path.join(tmp, "sig")
            with open(sigfile, "wb") as handle:
                handle.write(decode(sig))
            verified = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", pub, "-signature",
                 sigfile], input=("%s.%s" % (head, claims)).encode(),
                capture_output=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_a_key_openssl_cannot_use_is_an_oserror(self):
        with tempfile.NamedTemporaryFile("w", suffix=".pem") as bad:
            bad.write("not a key\n")
            bad.flush()
            with self.assertRaises(OSError):
                github.make_jwt(1, bad.name)


class Mint(Case):

    def test_mints_after_checking_the_slug(self):
        gh = FakeGitHub()
        tok = github.mint(self.project, "LeadDev", gh, fake_sign)
        self.assertEqual(tok["token"], "ghs_x")
        self.assertEqual([c[0] for c in gh.calls], ["GET", "POST"])
        self.assertTrue(gh.calls[1][1].endswith(
            "/app/installations/201/access_tokens"))

    def test_mints_nothing_when_the_key_names_another_app(self):
        gh = FakeGitHub(slug="reviewerwidgetagent")
        with self.assertRaises(github.MintError) as caught:
            github.mint(self.project, "LeadDev", gh, fake_sign)
        self.assertEqual(caught.exception.status, github.REFUSED)
        self.assertIn("belongs to reviewerwidgetagent, not leaddevwidgetagent",
                      str(caught.exception))
        self.assertEqual(len(gh.calls), 1)

    def test_a_named_slug_is_the_one_expected(self):
        tok = github.mint(self.project, "Helper", FakeGitHub(slug="helperbot"),
                          fake_sign)
        self.assertEqual(tok["token"], "ghs_x")

    def statuses(self, name, gh):
        with self.assertRaises(github.MintError) as caught:
            github.mint(self.project, name, gh, fake_sign)
        return caught.exception.status, str(caught.exception)

    def test_each_failure_has_its_status(self):
        self.assertEqual(self.statuses("Nobody", FakeGitHub())[0],
                         github.NO_SUCH_APP)
        status, message = self.statuses("UIReviewer", FakeGitHub())
        self.assertEqual(status, github.NOT_MADE)
        self.assertIn("app_id and installation_id is null", message)
        self.assertEqual(self.statuses("UIDev", FakeGitHub())[0],
                         github.UNREADABLE)
        self.assertEqual(self.statuses("LeadDev", FakeGitHub(app=0))[0],
                         github.UNREACHABLE)
        self.assertEqual(self.statuses("LeadDev", FakeGitHub(app=401))[0],
                         github.REFUSED)
        self.assertEqual(self.statuses("LeadDev", FakeGitHub(tokens=0))[0],
                         github.UNREACHABLE)
        self.assertEqual(self.statuses("LeadDev", FakeGitHub(tokens=404))[0],
                         github.REFUSED)

    def test_an_app_not_made_is_refused_before_its_key_is_read(self):
        def sign(*args):
            raise AssertionError("signed")
        with self.assertRaises(github.MintError):
            github.mint(self.project, "UIReviewer", FakeGitHub(), sign)

    def test_no_key_dir(self):
        del self.project.data["key_dir"]
        self.assertEqual(self.statuses("LeadDev", FakeGitHub())[0],
                         github.UNREADABLE)

    def test_missing_ids(self):
        self.assertEqual(github.missing_ids({"app_id": True,
                                             "installation_id": 3}),
                         ["app_id"])
        self.assertEqual(github.missing_ids({"app_id": 0}),
                         ["app_id", "installation_id"])


class Check(Case):

    def test_good(self):
        line, ok = github.check(self.project, "LeadDev", FakeGitHub(),
                                fake_sign)
        self.assertTrue(ok)
        self.assertEqual(line, "app 101 slug leaddevwidgetagent installation "
                               "201 on example")

    def test_installation_refused(self):
        line, ok = github.check(self.project, "LeadDev",
                                FakeGitHub(installation=404), fake_sign)
        self.assertFalse(ok)
        self.assertEqual(line, "installation 201 refused (404)")

    def test_a_mint_failure_is_the_line(self):
        line, ok = github.check(self.project, "UIReviewer", FakeGitHub(),
                                fake_sign)
        self.assertFalse(ok)
        self.assertIn("not made yet", line)


class Calls(unittest.TestCase):

    def test_rest_and_graphql_as_an_installation(self):
        gh = FakeGitHub()
        github.rest("t", "PATCH", "/repos/o/r/pulls/1", {"title": "x"}, gh)
        github.graphql("t", "query{viewer{login}}", {"a": 1}, gh)
        self.assertEqual(gh.calls, [
            ("PATCH", "https://api.github.com/repos/o/r/pulls/1", "token",
             {"title": "x"}),
            ("POST", "https://api.github.com/graphql", "token",
             {"query": "query{viewer{login}}", "variables": {"a": 1}})])

    def test_no_answer_is_status_0(self):
        status, body = github.api_call("http://127.0.0.1:9/", "t")
        self.assertEqual(status, 0)
        self.assertIn("message", body)


if __name__ == "__main__":
    unittest.main()
