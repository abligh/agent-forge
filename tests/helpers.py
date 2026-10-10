"""What several test files share."""
import copy
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "config.json"


def example():
    """A fresh copy of the example configuration."""
    return copy.deepcopy(json.loads(EXAMPLE.read_text()))


def done(args, rc=0, out="", err=""):
    return subprocess.CompletedProcess(args, rc, out, err)


def load_tool(name):
    """bin/<name>, an extensionless script, as a module."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(
        name.replace("-", "_"), str(ROOT / "bin" / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Repo:
    """Throwaway git repositories, with the user's own git config kept
    out of them."""

    def __init__(self, test):
        import os
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.env = dict(os.environ, HOME=tmp.name,
                        GIT_CONFIG_GLOBAL=os.path.join(tmp.name, "gitconfig"),
                        GIT_CONFIG_NOSYSTEM="1")
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(key, None)

    def git(self, cwd, *args, check=True, input=None):
        got = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                             text=True, env=self.env, input=input)
        if check and got.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), got.stderr))
        return got

    def run(self, cwd):
        """A `run` for tools, in `cwd`, with this environment."""
        def run(args, cwd_=None, env=None, timeout=None, **_):
            return subprocess.run(args, cwd=cwd_ or cwd, capture_output=True,
                                  text=True, env=env or self.env,
                                  timeout=timeout)
        return run
