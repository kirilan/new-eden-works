"""The plugins' agent skills: discovery, MCP prompts and `neworks agent install` (step 8)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from newedenworks import agent, mcp, registry

CORE_SKILLS = {"ops-check": "core", "industry-jobs": "industry", "supply-run": "industry"}


class DiscoveryTests(unittest.TestCase):
    def test_builtin_plugins_ship_their_skills(self):
        found = {s.name: s.plugin for s in agent.skills(registry.load(registry.builtin_candidates(), enabled=None))}
        self.assertEqual(found, CORE_SKILLS)

    def test_a_disabled_plugin_ships_none(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=["legacy"])
        self.assertEqual([s.name for s in agent.skills(loaded)], ["ops-check"])

    def test_every_skill_has_front_matter_and_names_no_old_paths(self):
        for skill in agent.skills(registry.load(enabled=None)):
            text = skill.text()
            meta = agent.front_matter(text)
            self.assertEqual(meta.get("name"), skill.name)
            self.assertGreater(len(meta.get("description", "")), 50, skill.name)
            for old in (".claude/skills", "job_board.py", "price_sell.py", "eve-skills ", "python -m eve_skills"):
                self.assertNotIn(old, text, f"{skill.name}: {old}")
            self.assertTrue(os.path.isfile(os.path.join(skill.path, "playbook.example.md")), skill.name)

    def test_front_matter(self):
        self.assertEqual(agent.front_matter("---\nname: x\ndescription: a: b\n---\nbody"), {"name": "x", "description": "a: b"})
        self.assertEqual(agent.front_matter("no front matter"), {})


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.found = agent.skills(registry.load(registry.builtin_candidates(), enabled=None))

    def test_install_copies_marks_and_replaces_only_its_own(self):
        dest = os.path.join(self.tmp.name, "skills")
        os.makedirs(os.path.join(dest, "supply-run"))                 # the user's own skill of that name
        done, skipped = agent.install(self.found, dest)
        self.assertEqual(sorted(done), ["industry-jobs", "ops-check"])
        self.assertEqual(len(skipped), 1)
        self.assertTrue(os.path.isfile(os.path.join(dest, "ops-check", "SKILL.md")))
        with open(os.path.join(dest, "ops-check", agent.MARKER), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["plugin"], "core")
        done, _ = agent.install(self.found, dest)                       # a reinstall replaces its own
        self.assertIn("ops-check", done)
        done, skipped = agent.install(self.found, dest, force=True)
        self.assertEqual((sorted(done), skipped), (["industry-jobs", "ops-check", "supply-run"], []))

    def test_destinations_and_config(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/c", "HERMES_HOME": "/h"}):
            self.assertEqual(agent.destination("codex"), os.path.join("/c", "skills"))
            self.assertEqual(agent.destination("hermes"), os.path.join("/h", "skills"))
        self.assertEqual(agent.destination("claude-code", cwd="/p"), os.path.join("/p", ".claude", "skills"))
        self.assertIn('"mcpServers"', agent.config_text("claude-code"))
        self.assertIn("[mcp_servers.neworks]", agent.config_text("codex"))
        self.assertIn("mcp_servers:", agent.config_text("hermes"))
        self.assertIn("mcp", agent.server_command())


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.server = mcp.Server(registry.load(registry.builtin_candidates(), enabled=None), cfg={})

    def call(self, method, params=None):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def test_prompts_are_the_skills_with_the_playbook_attached(self):
        prompts = self.call("prompts/list")["result"]["prompts"]
        self.assertEqual(sorted(p["name"] for p in prompts), sorted(CORE_SKILLS))
        self.assertEqual(prompts[0]["arguments"][0]["name"], "site")
        with tempfile.TemporaryDirectory() as home:
            book = os.path.join(home, "eve-skills", "playbook")
            os.makedirs(book)
            with open(os.path.join(book, "ops-check.md"), "w", encoding="utf-8") as fh:
                fh.write("# our rules")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
                got = self.call("prompts/get", {"name": "ops-check", "arguments": {"site": "home"}})["result"]
        text = got["messages"][0]["content"]["text"]
        self.assertIn("Work on site home.", text)
        self.assertIn("# Ops check", text)
        self.assertNotIn("description:", text)                         # the front matter is left out
        self.assertEqual(got["messages"][1]["content"]["resource"]["text"], "# our rules")
        self.assertEqual(self.call("prompts/get", {"name": "nope"})["error"]["code"], mcp.INVALID_PARAMS)

    def test_each_skill_is_a_resource(self):
        uris = [r["uri"] for r in self.call("resources/list")["result"]["resources"]]
        self.assertIn("newedenworks://skills/ops-check", uris)


if __name__ == "__main__":
    unittest.main()
