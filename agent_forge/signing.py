"""Sign-offs: how a body says which agent wrote it.

Every agent session on a host may share one gh login, so on GitHub the
sign-off line, `— <Agent>`, can be the only attribution a body carries.
The rules here make it hard to send one unsigned, signed twice, or
signed as someone else:

* the agent's name comes from FORGE_AGENT and is never guessed; a tool
  that quietly signs as somebody else forges the attribution;
* the sign-off is appended by the tool, never handed out pre-signed to
  be edited, since a truncating write loses it;
* every trailing sign-off is stripped before exactly one is appended,
  so a caller who also signs gets one, and a sign-off quoted mid-body
  can't stand in for the real one;
* `@word` outside code is refused: agents aren't GitHub users, so a
  mention notifies an unrelated stranger who has that login.
"""

import os
import re

DASH = "—"
AGENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# GitHub treats `@` as a mention only at a word boundary, so
# `serde@1.0.210` and `owner/repo@abc123` are not mentions.
MENTION_RE = re.compile(r"(^|[^A-Za-z0-9_/-])@([A-Za-z][A-Za-z0-9-]*)",
                        re.MULTILINE)
CODE_HANDLE_RE = re.compile(r"`@[A-Za-z][A-Za-z0-9-]*`")


class SigningError(Exception):
    """`status` is the exit status a tool reports it with."""

    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def agent(environ=None):
    """FORGE_AGENT, checked to be a bare name; SigningError(2) if not."""
    environ = os.environ if environ is None else environ
    name = environ.get("FORGE_AGENT", "")
    if not name:
        raise SigningError(2, "set FORGE_AGENT to your own agent name; this "
                           "signs on your behalf and will not guess")
    if not AGENT_RE.match(name):
        raise SigningError(2, "FORGE_AGENT must be a bare name (letters, "
                           "digits, _ or -), got %r" % name)
    return name


def sign_off(name):
    return "%s %s" % (DASH, name)


def signed(body, name):
    """`body` with its trailing blank lines and sign-offs removed, then
    exactly one sign-off appended after a blank line."""
    line = re.escape(sign_off(name))
    body = re.sub(r"(?:\s*\n%s[ \t]*)+\s*\Z" % line, "\n", "\n" + body)[1:]
    return body.rstrip() + "\n\n" + sign_off(name) + "\n"


def is_signed(body, name):
    """Whether a line of `body` is exactly the agent's sign-off."""
    return sign_off(name) in body.splitlines()


def outside_code(body):
    """`body` without fenced blocks and inline code spans. If the fences
    don't pair, the whole body: an unterminated fence would otherwise
    hide everything after it from the check."""
    lines = body.splitlines()
    if sum(1 for l in lines if l.startswith("```")) % 2:
        return body
    kept, fenced = [], False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(kept)


def mentions(body):
    """(line number, @handle) for each mention outside code."""
    found = []
    for number, line in enumerate(outside_code(body).splitlines(), 1):
        for m in MENTION_RE.finditer(line):
            found.append((number, "@" + m.group(2)))
    return found


def code_handles(body):
    """Backticked `@handles`: inert in a comment, but live in a squash
    commit message, which isn't rendered as markdown."""
    return sorted(set(CODE_HANDLE_RE.findall(body)))
