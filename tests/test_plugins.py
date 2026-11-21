"""The plugin contract and registry."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from newedenworks import cli, config, doctor, registry
from newedenworks.plugin import API_VERSION, Check, Command, ConfigSpec, Plugin, Schema


# The built-in plugins in load order (registry.BUILTIN), core last.
BUILT = [target.partition(":")[0].rpartition(".")[2] for target in registry.BUILTIN]
BUILT = [{"cmd_plugins": "core", "cmd_dashboard": "dashboard"}.get(name, name) for name in BUILT]


def candidate(make_plugin, target="tests:plugin", source="entry point (test 1.0)"):
    return registry.Candidate(source, target, lambda: make_plugin)


def plain(name, *, api=API_VERSION, commands=(), requires=(), enhances=(), hooks=None):
    return lambda: Plugin(name=name, version="1.0", api=api, commands=tuple(commands),
                          requires=tuple(requires), enhances=tuple(enhances), hooks=hooks or {})


def handled(*path, calls=None, default=False):
    def handler(args):
        if calls is not None:
            calls.append((path, args))
        return 0
    return Command(tuple(path), help=f"{' '.join(path)} help", handler=handler, default=default)


def run_main(argv, loaded):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli.registry_mod, "current", return_value=loaded), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class LoadTests(unittest.TestCase):
    def test_builtins_register_every_command_once(self):
        loaded = registry.load(registry.builtin_candidates())
        self.assertEqual([p.plugin.name for p in loaded.plugins], BUILT)
        self.assertEqual(loaded.refused, [])
        self.assertEqual(loaded.provider(("industry",)), "industry")
        self.assertEqual(loaded.provider(("ledger",)), "ledger")
        self.assertEqual(loaded.provider(("inventory",)), "legacy")
        self.assertIsNone(loaded.provider(("orders",)), "trade is never built in: an entry point plugin")
        self.assertEqual(loaded.provider(("skills",)), "characters")
        self.assertEqual(loaded.provider(("plugins",)), "core")
        self.assertEqual(loaded.default_command().path, ("skills",))

    def test_newer_api_is_refused_with_a_reason(self):
        loaded = registry.load([candidate(plain("future", api=API_VERSION + 1))])
        self.assertEqual(loaded.plugins, [])
        self.assertIn(f"needs plugin API {API_VERSION + 1}", loaded.refused[0].reason)

    def test_not_a_plugin_and_import_errors_are_refused(self):
        def broken():
            raise ImportError("no module named thing")
        loaded = registry.load([candidate(lambda: "nope", target="a:b"),
                                registry.Candidate("entry point", "c:d", broken)])
        self.assertEqual([r.name for r in loaded.refused], ["a:b", "c:d"])
        self.assertIn("not a Plugin", loaded.refused[0].reason)
        self.assertIn("ImportError", loaded.refused[1].reason)

    def test_a_broken_builtin_fails_loudly(self):
        def broken():
            raise ImportError("bug")
        with self.assertRaises(ImportError):
            registry.load([registry.Candidate("built-in", "newedenworks.x:plugin", broken)])

    def test_duplicate_command_refuses_the_later_plugin_only(self):
        loaded = registry.load([candidate(plain("one", commands=[handled("go")])),
                                candidate(plain("two", commands=[handled("go"), handled("other")]))])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["one"])
        self.assertIn("go is already registered by one", loaded.refused[0].reason)
        self.assertIsNone(loaded.command(("other",)), "a refused plugin registers nothing")

    def test_duplicate_plugin_name_is_refused(self):
        loaded = registry.load([candidate(plain("one")), candidate(plain("one"))])
        self.assertEqual(len(loaded.plugins), 1)
        self.assertIn("already loaded", loaded.refused[0].reason)

    def test_missing_requirement_cascades(self):
        loaded = registry.load([candidate(plain("dashboard", requires=["ledger"])),
                                candidate(plain("extra", requires=["dashboard"])),
                                candidate(plain("standalone"))])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["standalone"])
        self.assertEqual({r.name for r in loaded.refused}, {"dashboard", "extra"})

    def test_requirement_refused_for_a_clash_strands_its_dependents(self):
        loaded = registry.load([candidate(plain("a", commands=[handled("x")])),
                                candidate(plain("b", commands=[handled("x")])),
                                candidate(plain("c", requires=["b"]))])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["a"])
        self.assertEqual([r.name for r in loaded.refused], ["b", "c"])

    def test_dependencies_load_first_and_soft_ones_are_optional(self):
        loaded = registry.load([candidate(plain("dashboard", requires=["ledger"], enhances=["trade", "absent"])),
                                candidate(plain("trade", requires=["ledger"])),
                                candidate(plain("ledger"))])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["ledger", "trade", "dashboard"])

    def test_a_cycle_refuses_only_its_members(self):
        loaded = registry.load([candidate(plain("a", requires=["b"])), candidate(plain("b", requires=["a"])),
                                candidate(plain("c"))])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["c"])
        self.assertEqual({r.name for r in loaded.refused}, {"a", "b"})
        self.assertIn("cycle", loaded.refused[0].reason)

    def test_enhancing_a_plugin_that_requires_you_is_not_a_cycle(self):
        loaded = registry.load([candidate(plain("industry", enhances=["ledger"])),
                                candidate(plain("ledger", requires=["industry"]))], enabled=None)
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["industry", "ledger"])
        self.assertEqual(loaded.refused, [])

    def test_hooks_are_named_by_plugin_and_absent_ones_are_none(self):
        signal = lambda type_id: "DIP"   # noqa: E731
        loaded = registry.load([candidate(plain("trade", hooks={"price_signal": signal}))])
        self.assertIs(loaded.hook("trade.price_signal"), signal)
        self.assertIsNone(loaded.hook("trade.listing_price"))
        self.assertIsNone(loaded.hook("missing.price_signal"))

    def test_run_without_render_is_a_declaration_error(self):
        bad = Command(("data",), run=lambda args, ctx: {})
        loaded = registry.load([candidate(plain("p", commands=[bad]))])
        self.assertIn("run but no render", loaded.refused[0].reason)

    def test_a_second_default_command_is_refused(self):
        loaded = registry.load([candidate(plain("a", commands=[handled("x", default=True)])),
                                candidate(plain("b", commands=[handled("y", default=True)]))])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["a"])


class EntryPointTests(unittest.TestCase):
    """A plugin installed as its own distribution is found through the entry point group."""

    def test_an_installed_entry_point_plugin_loads_after_the_builtins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo_plugin.py").write_text(textwrap.dedent("""
                from newedenworks.plugin import API_VERSION, Command, Plugin

                def plugin():
                    return Plugin(name="demo", version="0.3", api=API_VERSION, summary="demo",
                                  commands=(Command(("demo",), help="demo help",
                                                    run=lambda args, ctx: {"ok": True},
                                                    render=lambda data, args: "ok"),))
            """))
            info = root / "demo_plugin-0.3.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Metadata-Version: 2.1\nName: demo-plugin\nVersion: 0.3\n")
            (info / "entry_points.txt").write_text(f"[{registry.ENTRY_POINT_GROUP}]\ndemo = demo_plugin:plugin\n")
            sys.path.insert(0, tmp)
            self.addCleanup(sys.modules.pop, "demo_plugin", None)
            self.addCleanup(sys.path.remove, tmp)
            found = [c for c in registry.entry_point_candidates() if c.target == "demo_plugin:plugin"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].source, "entry point (demo-plugin 0.3)")
            loaded = registry.load([*registry.builtin_candidates(), *found])
            self.assertEqual([p.plugin.name for p in loaded.plugins], BUILT + ["demo"])
            self.assertEqual(run_main(["demo"], loaded), (0, "ok\n", ""))


class DispatchTests(unittest.TestCase):
    def test_nested_data_command_gets_json_for_free(self):
        def signal_data(args, ctx):
            return {"type": args.type, "state": ctx.hook("trade.state")()}
        commands = [Command(("prices",), help="prices group"),
                    Command(("prices", "signal"), help="signal help",
                            configure=lambda p: p.add_argument("type"),
                            run=signal_data, render=lambda data, args: f"{data['type']}: {data['state']}")]
        loaded = registry.load([candidate(plain("trade", commands=commands, hooks={"state": lambda: "DIP"}))])
        self.assertEqual(run_main(["prices", "signal", "Tritanium"], loaded), (0, "Tritanium: DIP\n", ""))
        code, out, _ = run_main(["prices", "signal", "Tritanium", "--json"], loaded)
        self.assertEqual((code, json.loads(out)), (0, {"type": "Tritanium", "state": "DIP"}))

    def test_implicit_group_and_handler_dispatch(self):
        calls = []
        loaded = registry.load([candidate(plain("p", commands=[handled("ledger", "note", "add", calls=calls),
                                                               handled("ledger", "pnl", calls=calls)]))])
        self.assertEqual(run_main(["ledger", "note", "add"], loaded)[0], 0)
        self.assertEqual(run_main(["ledger", "pnl"], loaded)[0], 0)
        self.assertEqual([path for path, _ in calls], [("ledger", "note", "add"), ("ledger", "pnl")])

    def test_default_command_runs_without_arguments(self):
        calls = []
        loaded = registry.load([candidate(plain("p", commands=[handled("skills", calls=calls, default=True)]))])
        self.assertEqual(run_main([], loaded)[0], 0)
        self.assertEqual(calls[0][1].command, "skills")

    def test_refused_plugins_are_reported_on_stderr(self):
        loaded = registry.load([*registry.builtin_candidates(), candidate(plain("future", api=99))])
        code, out, err = run_main(["plugins", "--json"], loaded)
        self.assertEqual(code, 0)
        self.assertIn("warning: plugin future", err)
        data = json.loads(out)
        self.assertEqual([p["name"] for p in data["plugins"]], BUILT)
        self.assertEqual(data["refused"][0]["name"], "future")
        legacy, characters = data["plugins"][0], data["plugins"][1]
        self.assertIn("inventory", legacy["commands"])
        self.assertEqual(sum(len(p["commands"]) for p in data["plugins"] if p["name"] != "core"), 30,
                         "the 34 commands of eve-skills, less trade's five, plus job-board")

    def test_plugins_text_names_each_plugin_and_its_commands(self):
        code, out, err = run_main(["plugins"], registry.load(registry.builtin_candidates()))
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"(?m)^legacy +0\.2\.0 +built-in +10 ")
        self.assertRegex(out, r"(?m)^ledger +0\.2\.0 +built-in +1 ")
        self.assertRegex(out, r"(?m)^industry +0\.2\.0 +built-in +8 ")
        self.assertRegex(out, r"(?m)^planetary +0\.2\.0 +built-in +2 ")
        self.assertRegex(out, r"(?m)^characters +0\.2\.0 +built-in +8 ")
        self.assertIn("core: plugins", out)


class LegacyParserTests(unittest.TestCase):
    """The parser built from the legacy plugin parses exactly as the hand-built one did."""

    def test_sample_command_lines_parse_to_the_expected_namespaces(self):
        parser = cli.build_parser(registry.load(registry.builtin_candidates()))
        args = parser.parse_args(["industry", "status", "--corp", "--json"])
        self.assertEqual((args.command, args.industry_action, args.corp, args.json), ("industry", "status", True, True))
        self.assertIsInstance(args, argparse.Namespace)



if __name__ == "__main__":
    unittest.main()


class CoreServiceTests(unittest.TestCase):
    """Step 3: plugin-declared scopes, schemas, config sections, enabled plugins and doctor checks."""

    def test_legacy_declares_every_scope_feature_and_the_four_schemas(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=None)
        from newedenworks import sso
        self.assertEqual(loaded.scopes(), {**sso.OPTIONAL_SCOPES, **loaded.scopes()})
        self.assertEqual(set(loaded.plugin("characters").scopes), {"attributes", "standings", "location", "clones"})
        with mock.patch.object(registry, "current", return_value=loaded):
            self.assertEqual(list(sso.feature_scopes()), list(sso.OPTIONAL_SCOPES), "the table's order, whoever declares")
        self.assertEqual(sorted(s.name for s in loaded.schemas()), ["industry", "ledger", "planetary"])
        self.assertEqual(loaded.plugin("ledger").requires, ("industry",))

    def test_scope_schema_table_and_name_clashes_are_refused(self):
        def schema(name, *tables):
            return Schema(name, ("SELECT 1;",), tables)
        loaded = registry.load([
            candidate(lambda: Plugin(name="a", version="1", api=1, scopes={"orders": ["x"]},
                                     tables=(schema("a", "t1"),))),
            candidate(lambda: Plugin(name="b", version="1", api=1, scopes={"orders": ["y"]})),
            candidate(lambda: Plugin(name="c", version="1", api=1, scopes={"orders": ["x"]})),
            candidate(lambda: Plugin(name="d", version="1", api=1, tables=(schema("a"),))),
            candidate(lambda: Plugin(name="e", version="1", api=1, tables=(schema("e", "t1"),))),
            candidate(lambda: Plugin(name="f", version="1", api=1, tables=(Schema("f", ()),))),
            candidate(lambda: Plugin(name="mcp", version="1", api=1)),
        ], enabled=None)
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["a", "c"])
        reasons = {r.name: r.reason for r in loaded.refused}
        self.assertIn("scope feature orders", reasons["b"])
        self.assertIn("schema a is already owned", reasons["d"])
        self.assertIn("table t1", reasons["e"])
        self.assertIn("no migrations", reasons["f"])
        self.assertIn("core config key", reasons["mcp"])

    def test_plugins_enabled_skips_the_others_and_core_always_loads(self):
        loaded = registry.load([*registry.builtin_candidates(), candidate(plain("extra")),
                                candidate(plain("needs", requires=["extra"]))], enabled=["legacy", "needs"])
        self.assertEqual([p.plugin.name for p in loaded.plugins], ["legacy", "core"])
        self.assertEqual([d.name for d in loaded.disabled], [n for n in BUILT if n not in ("legacy", "core")] + ["extra"])
        self.assertIsNone(loaded.command(("skills",)), "a disabled plugin registers no commands")
        self.assertIn("requires extra", loaded.refused[0].reason)

    def test_enabled_list_comes_from_config_and_bad_values_load_everything(self):
        self.assertEqual(config.enabled_plugins({"plugins": {"enabled": ["legacy"]}}), ["legacy"])
        self.assertIsNone(config.enabled_plugins({}))
        self.assertIsNone(config.enabled_plugins({"plugins": {"enabled": "legacy"}}))
        self.assertIsNone(config.enabled_plugins({"plugins": "all"}))

    def test_sections_merge_over_defaults_and_problems_are_reported(self):
        spec = ConfigSpec(defaults={"job_notice_minutes": 20, "job_alerts": True},
                          validate=lambda s: [] if isinstance(s["job_notice_minutes"], int) else
                          ["job_notice_minutes must be a whole number of minutes"])
        loaded = registry.load([candidate(lambda: Plugin(name="industry", version="1", api=1, config=spec)),
                                candidate(plain("other"))], enabled=None)
        cfg = {"industry": {"job_notice_minutes": "soon"}, "other": 3,
               "plugins": {"enabled": ["industry", "other", "ghost"]}}
        self.assertEqual(config.section(loaded.plugin("industry"), {}), {"job_notice_minutes": 20, "job_alerts": True})
        self.assertEqual(config.section(loaded.plugin("industry"), {"industry": {"job_alerts": False}})["job_alerts"], False)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            found = config.problems(loaded, cfg)        # no alerts.json there: the sections are checked
        self.assertEqual(found, [
            ("plugins", "plugins.enabled names ghost, which is not installed"),
            # the alert settings' own check: job_notice_minutes is also the industry job alerts' key
            ("industry", "config.json: industry.job_notice_minutes must be a number"),
            ("industry", "job_notice_minutes must be a whole number of minutes"),
            ("other", "the other section must be an object"),
        ])
        self.assertEqual(config.problems(loaded, {"plugins": {"enabled": 5}})[0][0], "plugins")

    def test_a_data_command_reads_its_own_section_through_the_context(self):
        spec = ConfigSpec(defaults={"greeting": "hello"})
        command = Command(("hi",), run=lambda args, ctx: ctx.config, render=lambda data, args: data["greeting"])
        loaded = registry.load([candidate(lambda: Plugin(name="greeter", version="1", api=1, config=spec,
                                                         commands=(command,)))], enabled=None)
        with mock.patch.object(config.sso, "load_config", return_value={"greeter": {"greeting": "o7"}}):
            self.assertEqual(run_main(["hi"], loaded), (0, "o7\n", ""))

    def test_doctor_reports_plugins_config_and_each_plugins_checks(self):
        checks = (Check("fine", lambda ctx: {"status": "ok", "detail": "all good", "rows": 3}),
                  Check("two", lambda ctx: [{"status": "warn", "detail": "a"}, {"status": "bogus"}]),
                  Check("crash", lambda ctx: 1 / 0))
        loaded = registry.load([*registry.builtin_candidates(),
                                candidate(lambda: Plugin(name="probe", version="1", api=1, checks=checks)),
                                candidate(plain("future", api=99))], enabled=None)
        with mock.patch.object(doctor.registry, "current", return_value=loaded), \
                mock.patch.object(config.sso, "load_config", return_value={"plugins": {"enabled": 1}}):
            listed = doctor._check_plugins()
        produced = {c["name"]: c for c in listed}
        self.assertEqual([c["status"] for c in listed if c["name"] == "plugin.probe.two"], ["warn", "fail"])
        self.assertEqual(produced["plugins"]["status"], "warn")
        self.assertIn("future", produced["plugins"]["detail"])
        self.assertEqual(produced["config.plugins"]["status"], "warn")
        self.assertEqual((produced["plugin.probe.fine"]["status"], produced["plugin.probe.fine"]["rows"]), ("ok", 3))
        self.assertEqual(produced["plugin.probe.crash"]["status"], "fail")

    def test_doctor_reports_ok_for_the_builtins(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=None)
        with mock.patch.object(doctor.registry, "current", return_value=loaded), \
                mock.patch.object(config.sso, "load_config", return_value={}):
            (check,) = doctor._check_plugins()
        self.assertEqual((check["name"], check["status"]), ("plugins", "ok"))
        self.assertEqual(check["loaded"], BUILT)


class CharactersPluginTests(unittest.TestCase):
    """Step 4: the first real plugin, and what disabling it leaves."""

    def test_disabling_characters_removes_its_commands_and_the_default(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=["legacy"])
        self.assertIsNone(loaded.default_command())
        code, out, _ = run_main([], loaded)
        self.assertEqual(code, 0)
        self.assertIn("usage: neworks", out, "with no default command the help is shown")
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            run_main(["skills"], loaded)
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("attributes", loaded.scopes())
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            run_main(["inventory", "--help"], loaded)
        self.assertEqual(caught.exception.code, 0, "the other plugins' commands still work")

    def test_converted_commands_gain_json_and_keep_their_csv(self):
        parser = cli.build_parser(registry.load(registry.builtin_candidates(), enabled=None))
        for argv in (["summary", "--json"], ["plan", "X", "--json"], ["extract", "--json"],
                     ["attributes", "--json"], ["standings", "--csv", "--json"], ["travel", "--json"],
                     ["implants", "--csv"]):
            parser.parse_args(argv)

    def test_csv_wins_over_json_and_watch_hands_over(self):
        seen = []
        command = Command(("view",), run=lambda args, ctx: {"a": 1}, render=lambda d, a: "text",
                          csv=lambda d, a: "a\n1\n", watch=lambda args: seen.append("watch") or 0,
                          configure=lambda p: p.add_argument("--watch", type=int))
        loaded = registry.load([candidate(plain("v", commands=[command]))], enabled=None)
        self.assertEqual(run_main(["view", "--csv", "--json"], loaded), (0, "a\n1\n", ""))
        self.assertEqual(run_main(["view", "--json"], loaded)[1], '{\n  "a": 1\n}\n')
        self.assertEqual(run_main(["view", "--watch", "5"], loaded), (0, "", ""))
        self.assertEqual(seen, ["watch"])


class DisableEachPluginTests(unittest.TestCase):
    """Step 5's bar: each built-in plugin can be disabled without breaking the others, except where a
    plugin declares `requires`."""

    def test_disabling_any_one_plugin_leaves_every_other_command_working(self):
        everything = registry.load(registry.builtin_candidates(), enabled=None)
        for off in (name for name in BUILT if name != "core"):
            with self.subTest(disabled=off):
                loaded = registry.load(registry.builtin_candidates(), enabled=[n for n in BUILT if n != off])
                refused = {r.name for r in loaded.refused}
                expected = {p.plugin.name for p in everything.plugins if off in p.plugin.requires}
                self.assertEqual(refused, expected, "only plugins that require it may go with it")
                gone = {off} | refused
                kept = [path for path, (_c, owner) in everything.commands.items() if owner not in gone]
                parser = cli.build_parser(loaded)
                for path in kept:
                    with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
                        parser.parse_args([*path, "--help"])
                    self.assertEqual(caught.exception.code, 0, f"{' '.join(path)} --help with {off} disabled")
                for path, (_c, owner) in everything.commands.items():
                    if owner in gone:
                        self.assertIsNone(loaded.command(path))
