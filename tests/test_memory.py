"""Tests for agent_forge.memory: what forge-memory-* agree on."""
import os
import tempfile
import unittest

from agent_forge import config, memory
from tests.helpers import example


def project(**section):
    data = example()
    if section:
        data["memory"] = section
    else:
        data.pop("memory", None)
    return config.Project(data, None)


ENV = {"HOME": "/home/agent"}


class Settings(unittest.TestCase):

    def test_the_configuration_with_defaults_and_home_expanded(self):
        cfg = memory.settings(project(dir="~/m", remote="git@h:o/r.git"),
                              ENV, hostname=lambda: "box.example.org")
        self.assertEqual(cfg["dir"], "/home/agent/m")
        self.assertEqual(cfg["remote"], "git@h:o/r.git")
        self.assertEqual(cfg["git_dir"], "/home/agent/.widget-memory.git")
        self.assertEqual(cfg["seed_dir"], "/home/agent/.widget-memory-seed")
        self.assertIsNone(cfg["ssh_key"])
        self.assertEqual(cfg["branch"], "main")
        self.assertEqual(cfg["labels"], [])
        self.assertEqual(cfg["limit"], memory.DEFAULT_LIMIT)
        self.assertEqual(cfg["long_line"], memory.LONG_LINE)
        self.assertEqual(cfg["host"], "box", "the short name")
        self.assertEqual(cfg["repo"], "example/widget")

    def test_every_key_from_the_configuration(self):
        cfg = memory.settings(project(
            dir="/m", remote="/r", git_dir="~/g", seed_dir="/s",
            ssh_key="~/.ssh/k", branch="trunk", labels=["bug"], limit=100,
            long_line=50), ENV)
        self.assertEqual(
            [cfg[k] for k in ("git_dir", "seed_dir", "ssh_key", "branch",
                              "labels", "limit", "long_line")],
            ["/home/agent/g", "/s", "/home/agent/.ssh/k", "trunk", ["bug"],
             100, 50])

    def test_each_variable_overrides_its_key(self):
        cfg = memory.settings(project(dir="/m", remote="/r"), dict(
            ENV, FORGE_MEMORY_DIR="/m2", FORGE_MEMORY_REMOTE="/r2",
            FORGE_MEMORY_GIT_DIR="/g2", FORGE_MEMORY_SEED_DIR="/s2",
            FORGE_MEMORY_SSH_KEY="/k2", FORGE_MEMORY_HOST="h2"))
        self.assertEqual([cfg[k] for k in ("dir", "remote", "git_dir",
                                           "seed_dir", "ssh_key", "host")],
                         ["/m2", "/r2", "/g2", "/s2", "/k2", "h2"])

    def test_the_environment_alone_is_enough(self):
        cfg = memory.settings(project(), dict(
            ENV, FORGE_MEMORY_DIR="/m", FORGE_MEMORY_REMOTE="/r"))
        self.assertEqual((cfg["dir"], cfg["remote"]), ("/m", "/r"))

    def test_without_a_directory_or_a_repository_it_says_which(self):
        with self.assertRaises(memory.NotConfigured) as got:
            memory.settings(project(), ENV)
        self.assertIn("memory.dir and memory.remote", str(got.exception))
        self.assertIn("FORGE_MEMORY_DIR and FORGE_MEMORY_REMOTE",
                      str(got.exception))
        with self.assertRaises(memory.NotConfigured) as got:
            memory.settings(project(), dict(ENV, FORGE_MEMORY_DIR="/m"))
        self.assertIn("no memory remote", str(got.exception))


class Directory(unittest.TestCase):

    def test_the_variable_then_the_configuration_then_none(self):
        self.assertEqual(memory.directory(project(dir="~/m", remote="/r"),
                                          dict(ENV, FORGE_MEMORY_DIR="/v")),
                         "/v")
        self.assertEqual(memory.directory(project(dir="~/m", remote="/r"),
                                          ENV), "/home/agent/m")
        self.assertIsNone(memory.directory(project(), ENV))
        self.assertIsNone(memory.directory(None, ENV))


class FiledPath(unittest.TestCase):

    def test_under_the_projects_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = memory.filed_path("widget", {"FORGE_STATE_DIR": tmp})
            self.assertEqual(path, os.path.join(tmp, "widget", "memory-sync",
                                                "filed.json"))


if __name__ == "__main__":
    unittest.main()
