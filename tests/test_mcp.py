"""The MCP server: protocol, tools, gating and resources."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from newedenworks import cli, config, mcp, registry
from newedenworks.plugin import API_VERSION, Plugin, Tool


def loaded_all():
    return registry.load(enabled=None)


def demo(*tools, name="demo"):
    return registry.Candidate("entry point (test 1.0)", f"{name}:plugin",
                              lambda: lambda: Plugin(name=name, version="1.0", api=API_VERSION, tools=tuple(tools)))


def sample(prop):
    kind = prop.get("type")
    if "enum" in prop:
        return prop["enum"][0]
    if kind == "array":
        return [1, 2] if prop["items"]["type"] == "integer" else ["a", "b"]
    return {"string": "x", "boolean": True, "integer": max(1, prop.get("minimum", 1)), "number": 1.5}[kind]


class FlagsTests(unittest.TestCase):
    def test_flags_build_the_command_line(self):
        build = mcp.flags("show", site="--site", corp="--corp", ids=None, extra="--extra", off="--off")
        self.assertEqual(build({"site": "home", "corp": True, "ids": ["1", "2"], "extra": ["a", "b"], "off": False}),
                         ["show", "--site", "home", "--corp", "--extra", "a", "--extra", "b", "1", "2"])
        self.assertEqual(build({}), ["show"])

    def test_arguments_are_checked_against_the_schema(self):
        tool = Tool("t", command=("x",), input_schema=mcp.schema(
            required=("name",), name=mcp.string("n"), n=mcp.integer("n", 1), by=mcp.string("b", ("day", "week")),
            on=mcp.boolean("o"), items=mcp.strings("i"), ids=mcp.integers("i")))
        self.assertIsNone(mcp.check_arguments(tool, {"name": "a", "n": 2, "by": "day", "on": True, "items": ["x"]}))
        for bad, why in (({}, "missing required argument name"), ({"name": "a", "zz": 1}, "unknown argument zz"),
                         ({"name": "a", "n": "2"}, "must be of type integer"), ({"name": "a", "n": True}, "integer"),
                         ({"name": "a", "n": 0}, "at least 1"), ({"name": "a", "by": "year"}, "one of day, week"),
                         ({"name": "a", "items": [1]}, "list of strings"), ({"name": "a", "ids": ["1"]}, "list of integers"), ([], "must be an object")):
            self.assertIn(why, mcp.check_arguments(tool, bad), bad)


class DeclaredToolsTests(unittest.TestCase):
    def test_every_tool_runs_a_command_line_the_cli_accepts(self):
        loaded = loaded_all()
        parser = cli.build_parser(loaded)
        tools = mcp.all_tools(loaded)
        self.assertGreater(len(tools), 20)
        for tool, plugin in tools:
            props = (tool.input_schema or {}).get("properties", {})
            for arguments in ({k: sample(props[k]) for k in (tool.input_schema or {}).get("required", [])},
                              {k: sample(p) for k, p in props.items()
                               if not (k in ("copies", "originals", "buy", "sell") and p["type"] == "boolean")}):
                self.assertIsNone(mcp.check_arguments(tool, arguments), (tool.name, arguments))
                argv = mcp.command_line(tool, arguments)
                with self.subTest(tool=tool.name, argv=argv):
                    with mock.patch("sys.stderr", io.StringIO()):
                        parser.parse_args(argv)
            self.assertTrue(tool.description, tool.name)

    def test_names_are_unique_and_valid(self):
        loaded = registry.load([demo(Tool("a_b", command=("x",))), demo(Tool("a_b", command=("y",)), name="other")],
                               enabled=None)
        self.assertIn("tool a_b is already declared by demo", loaded.refused[0].reason)
        for bad in (Tool("Bad-Name", command=("x",)), Tool("ok", command=())):
            self.assertIsNone(registry.load([demo(bad)], enabled=None).plugin("demo"))


class GatingTests(unittest.TestCase):
    def test_writes_are_offered_only_when_allowed(self):
        loaded = loaded_all()
        names = {t.name for t, _ in mcp.offered(loaded, {})}
        self.assertIn("ledger_progress", names)
        self.assertNotIn("ledger_note_add", names)
        names = {t.name for t, _ in mcp.offered(loaded, {"mcp": {"allow_writes": ["ledger_note_add"]}})}
        self.assertIn("ledger_note_add", names)
        self.assertNotIn("ledger_sync", names)

    def test_doctor_reports_bad_allow_writes(self):
        loaded = loaded_all()
        self.assertEqual(config.problems(loaded, {"mcp": {"allow_writes": ["ledger_sync"]}}), [])
        found = config.problems(loaded, {"mcp": {"allow_writes": ["nope", "ledger_pnl"]}})
        self.assertEqual([owner for owner, _ in found], ["mcp", "mcp"])
        self.assertEqual(len(config.problems(loaded, {"mcp": {"allow_writes": "all"}})), 1)


def call(server, method, params=None, ident=1):
    message = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.runs = []

        def runner(argv, **kw):
            self.runs.append(argv)
            if "fail" in argv:
                return SimpleNamespace(returncode=1, stdout="", stderr="trace\nerror: it broke")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": argv[-2]}), stderr="warning: stale")

        tools = (Tool("echo", command=("echo",), description="d", input_schema=mcp.schema(word=mcp.string("w")),
                      args=mcp.flags(word=None)),
                 Tool("raw", command=("raw",), description="d", json=False, args=mcp.flags()),
                 Tool("write", command=("w",), description="d", read_only=False))
        self.loaded = registry.load([demo(*tools)], enabled=None)
        self.server = mcp.Server(self.loaded, cfg={}, runner=runner)

    def test_initialize_negotiates_the_version(self):
        result = call(self.server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "new-eden-works")
        self.assertIn("tools", result["capabilities"])
        newest = call(self.server, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(newest["protocolVersion"], mcp.PROTOCOL_VERSIONS[0])

    def test_notifications_get_no_answer_and_unknown_methods_an_error(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(call(self.server, "nope")["error"]["code"], mcp.METHOD_NOT_FOUND)
        self.assertEqual(self.server.handle({"id": 1})["error"]["code"], mcp.INVALID_REQUEST)
        self.assertEqual(call(self.server, "ping")["result"], {})

    def test_tools_list_hides_writes_and_carries_hints(self):
        tools = call(self.server, "tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["echo", "raw"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        self.assertEqual(tools[0]["inputSchema"]["properties"]["word"]["type"], "string")

    def test_a_call_runs_the_command_with_json(self):
        result = call(self.server, "tools/call", {"name": "echo", "arguments": {"word": "hi"}})["result"]
        self.assertEqual(self.runs[-1][-3:], ["echo", "hi", "--json"])
        self.assertEqual(result["structuredContent"], {"ok": "hi"})
        self.assertFalse(result["isError"])
        self.assertIn("warning: stale", result["content"][1]["text"])
        self.assertEqual(result["content"][0]["text"], '{"ok":"hi"}')       # compact
        with mock.patch.object(mcp, "MAX_RESULT_CHARS", 5):
            big = call(self.server, "tools/call", {"name": "echo", "arguments": {"word": "hi"}})["result"]
        self.assertTrue(big["isError"])
        self.assertIn("narrow the call", big["content"][0]["text"])
        raw = call(self.server, "tools/call", {"name": "raw"})["result"]
        self.assertEqual(self.runs[-1][-1], "raw")
        self.assertNotIn("structuredContent", raw)

    def test_failures_are_tool_errors(self):
        result = call(self.server, "tools/call", {"name": "echo", "arguments": {"word": "fail"}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("error: it broke", result["content"][0]["text"])
        bad = call(self.server, "tools/call", {"name": "echo", "arguments": {"x": 1}})["result"]
        self.assertTrue(bad["isError"])
        self.assertEqual(call(self.server, "tools/call", {"name": "write"})["error"]["code"], mcp.INVALID_PARAMS)

    def test_serve_reads_lines_and_writes_one_answer_each(self):
        lines = "\n".join([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "not json",
                           json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), ""])
        out = io.StringIO()
        self.server.serve(io.StringIO(lines), out)
        answers = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([a.get("id") for a in answers], [1, None])
        self.assertEqual(answers[1]["error"]["code"], mcp.PARSE_ERROR)


class ResourceTests(unittest.TestCase):
    def test_playbooks_and_operation_come_from_the_config_folder(self):
        with tempfile.TemporaryDirectory() as home:
            config_dir = os.path.join(home, "eve-skills")
            os.makedirs(os.path.join(config_dir, "playbook"))
            with open(os.path.join(config_dir, "playbook", "ops-check.md"), "w", encoding="utf-8") as fh:
                fh.write("# rules")
            with open(os.path.join(config_dir, "operation.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
                server = mcp.Server(registry.load(registry.builtin_candidates(), enabled=None), cfg={})
                uris = [r["uri"] for r in call(server, "resources/list")["result"]["resources"]]
                self.assertIn("newedenworks://playbook/ops-check", uris)
                self.assertIn("newedenworks://operation", uris)
                text = call(server, "resources/read", {"uri": "newedenworks://playbook/ops-check"})["result"]
                self.assertEqual(text["contents"][0]["text"], "# rules")
                self.assertEqual(call(server, "resources/read", {"uri": "newedenworks://nope"})["error"]["code"], -32002)


if __name__ == "__main__":
    unittest.main()
