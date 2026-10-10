"""Talking to GitHub as an App.

Every agent has its own GitHub App, because the App is the identity
GitHub records: an installation token minted from an agent's App acts
as that App's bot, and nothing it does is attributable to the owner's
account. This module is the one place that turns an agent's name into
such a token, so the JWT dance (a ten-minute assertion, an hour-long
installation token, nothing written to disk) exists once.

**The key is trusted to name itself, not the file name.** Before
minting, the assertion asks GitHub which App it belongs to, and the
answer must be the slug the configuration expects. A key filed under
the wrong name would otherwise make one agent act as another, invisibly
in everything it then produces. The check costs one request the
assertion is already good for.

**An App whose ids are null isn't made yet,** and is refused before its
key is read, with its own status.

Signing uses `openssl`, so nothing beyond the standard library is
needed. No message here ever contains a token.
"""

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

# How long one request may take. Without a bound, a connection GitHub
# stopped answering holds its caller until the kernel gives up, and a
# scheduled job doing so holds its lock all that time.
REQUEST_TIMEOUT_S = 30

# Why a mint failed, as the tools' exit statuses.
NO_SUCH_APP = 1
UNREADABLE = 2
REFUSED = 3
UNREACHABLE = 5
NOT_MADE = 6


def api_base(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("FORGE_GITHUB_API", "https://api.github.com")


class MintError(Exception):
    """A token could not be minted. `status` is the exit status a tool
    reports it with."""

    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def api_call(url, token, scheme="Bearer", method="GET", body=None):
    """One request. Returns (status, decoded body or {}); never raises
    for an HTTP error. Status 0 means no answer: the network failed, or
    GitHub didn't reply within REQUEST_TIMEOUT_S."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, method=method, data=data,
        headers={"Authorization": "%s %s" % (scheme, token),
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json",
                 "User-Agent": "agent-forge"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
    except (urllib.error.URLError, OSError) as err:
        # OSError too: a timeout while reading is a bare TimeoutError.
        return 0, {"message": str(err)}


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def openssl_sign(key_path, message):
    """RS256: an RSA PKCS#1 v1.5 signature over SHA-256, by openssl."""
    got = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_path],
                         input=message, capture_output=True, timeout=30)
    if got.returncode != 0:
        raise OSError("openssl could not sign with %s: %s"
                      % (key_path, got.stderr.decode(errors="replace")
                         .strip()))
    return got.stdout


def make_jwt(app_id, key_path, now=None, sign=openssl_sign):
    """A ten-minute assertion for App `app_id`, signed with its key.
    `iat` is a minute early, since GitHub refuses one from its future
    and the two clocks are not the same clock."""
    now = int(time.time() if now is None else now)
    header = b64url(b'{"alg":"RS256","typ":"JWT"}')
    claims = b64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                "iss": str(app_id)},
                               separators=(",", ":")).encode())
    signing_input = ("%s.%s" % (header, claims)).encode()
    return "%s.%s" % (signing_input.decode(),
                      b64url(sign(key_path, signing_input)))


def missing_ids(entry):
    """The ids `entry` lacks: each must be a positive integer."""
    return [k for k in ("app_id", "installation_id")
            if not (isinstance(entry.get(k), int)
                    and not isinstance(entry.get(k), bool)
                    and entry.get(k) > 0)]


def assertion_for(project, name, call=api_call, sign=openssl_sign):
    """(assertion, the App GitHub says it is) for `name`, after checking
    the App is made, its key readable and its slug the expected one;
    MintError otherwise."""
    try:
        entry = project.app(name)
    except KeyError:
        raise MintError(NO_SUCH_APP, "no agent or machinery named %r in %s"
                        % (name, project.path)) from None
    missing = missing_ids(entry)
    if missing:
        raise MintError(NOT_MADE, "%s's App is not made yet: %s is null in "
                        "%s" % (name, " and ".join(missing), project.path))
    key = project.key_path(name)
    if key is None:
        raise MintError(UNREADABLE, "no key_dir in %s" % project.path)
    if not os.path.exists(key):
        raise MintError(UNREADABLE, "no key at %s" % key)
    try:
        assertion = make_jwt(entry["app_id"], key, sign=sign)
    except OSError as err:
        raise MintError(UNREADABLE, str(err)) from None
    status, app = call("%s/app" % api_base(), assertion)
    if status == 0:
        raise MintError(UNREACHABLE, "could not reach GitHub to mint %s's "
                        "token: %s" % (name, app.get("message", "")))
    if status != 200:
        raise MintError(REFUSED, "GitHub refused %s's App (%s)"
                        % (name, status))
    if app.get("slug") != project.slug(name):
        raise MintError(REFUSED, "the key at %s belongs to %s, not %s"
                        % (key, app.get("slug"), project.slug(name)))
    return assertion, app


def mint(project, name, call=api_call, sign=openssl_sign):
    """The installation token object for `name` (with `token` and
    `expires_at`), or MintError."""
    assertion, _ = assertion_for(project, name, call, sign)
    status, tok = call("%s/app/installations/%d/access_tokens"
                       % (api_base(), project.app(name)["installation_id"]),
                       assertion, method="POST")
    if status == 0:
        raise MintError(UNREACHABLE, "could not reach GitHub to mint %s's "
                        "token" % name)
    if status != 201 or "token" not in tok:
        raise MintError(REFUSED, "GitHub refused %s's installation token "
                        "(%s)" % (name, status))
    return tok


def token(project, name, call=api_call, sign=openssl_sign):
    """Just the token string; see mint()."""
    return mint(project, name, call, sign)["token"]


def check(project, name, call=api_call, sign=openssl_sign):
    """One line on whether `name`'s entry agrees with GitHub, and
    whether it does."""
    try:
        assertion, app = assertion_for(project, name, call, sign)
    except MintError as err:
        return str(err), False
    entry = project.app(name)
    status, inst = call("%s/app/installations/%d"
                        % (api_base(), entry["installation_id"]), assertion)
    if status != 200:
        return ("installation %d refused (%s)"
                % (entry["installation_id"], status)), False
    return ("app %d slug %s installation %d on %s"
            % (entry["app_id"], app["slug"], entry["installation_id"],
               (inst.get("account") or {}).get("login"))), True


def rest(token_, method, path, body=None, call=api_call):
    """(status, body) for a REST call on `path` ("/repos/o/r/..."),
    as an installation."""
    return call(api_base() + path, token_, scheme="token", method=method,
                body=body)


def graphql(token_, query, variables=None, call=api_call):
    """(status, body) for a GraphQL query, as an installation."""
    return call(api_base() + "/graphql", token_, scheme="token",
                method="POST", body={"query": query,
                                     "variables": variables or {}})
