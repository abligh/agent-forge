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
