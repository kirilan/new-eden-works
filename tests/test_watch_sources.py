"""Watch sources: the plugins' parts of the watch loop."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from newedenworks import cmd_watch, registry
from newedenworks.cmd_watch import WatchCycle, WatchSession
from newedenworks.plugin import API_VERSION, Plugin, WatchSource


def with_sources(*sources, name="demo"):
    return registry.Candidate("entry point (test 1.0)", f"{name}:plugin",
                              lambda: lambda: Plugin(name=name, version="1.0", api=API_VERSION, watch=tuple(sources)))


def poll(**kw):
    return lambda args, client, session: WatchCycle(**kw)


class RegistryTests(unittest.TestCase):
    def test_builtin_sources(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=None)
        self.assertEqual([(s.name, p) for s, p in loaded.watch_sources()],
                         [("orders", "legacy"), ("skills", "characters"), ("colonies", "planetary"),
                          ("ledger", "ledger")])
        self.assertEqual(loaded.provider(("watch",)), "core")

    def test_a_second_source_of_one_name_is_refused(self):
        loaded = registry.load([with_sources(WatchSource("x", poll()), name="one"),
                                with_sources(WatchSource("x", poll()), name="two")], enabled=None)
        self.assertIsNone(loaded.plugin("two"))
        self.assertIn("watch source x is already declared by one", loaded.refused[0].reason)

    def test_disabling_a_plugin_drops_its_source(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=["legacy", "characters"])
        self.assertEqual([s.name for s, _ in loaded.watch_sources()], ["orders", "skills"])


class ChooseTests(unittest.TestCase):
    def setUp(self):
        self.loaded = registry.load(registry.builtin_candidates(), enabled=None)

    def names(self, args, only=None):
        return [s.name for s in cmd_watch.chosen_sources(self.loaded, args, only)]

    def test_skills_first_then_the_rest(self):
        args = SimpleNamespace(no_orders=False, no_colonies=False, ledger=False)
        self.assertEqual(self.names(args), ["skills", "orders", "colonies"])
        self.assertEqual(self.names(SimpleNamespace(no_orders=True, no_colonies=True, ledger=True)),
                         ["skills", "ledger"])

    def test_only(self):
        self.assertEqual(self.names(SimpleNamespace(), only=("orders",)), ["orders"])
        self.assertEqual(self.names(SimpleNamespace(), only=("missing",)), [])

    def test_unknown_sources_follow_in_load_order(self):
        loaded = registry.load([*registry.builtin_candidates(),
                                with_sources(WatchSource("zz", poll()), WatchSource("aa", poll()))], enabled=None)
        names = [s.name for s in cmd_watch.chosen_sources(loaded, SimpleNamespace())]
        self.assertEqual(names, ["skills", "orders", "colonies", "zz", "aa"])


class CombineTests(unittest.TestCase):
    def test_parts_in_source_order(self):
        cycles = [WatchCycle(warnings=("a",), observations=(1,), body="skills", idle="no data"),
                  WatchCycle(warnings=("b",), order_observations=(2,), body="", names={1: "x"}),
                  WatchCycle(colony_observations=(3,), body="colonies", names={2: "y"})]
        frame = cmd_watch.combine("watch", cycles)
        self.assertEqual(frame.title, "watch")
        self.assertEqual(frame.warnings, ("a", "b"))
        self.assertEqual((frame.observations, frame.order_observations, frame.colony_observations), ((1,), (2,), (3,)))
        self.assertEqual(frame.body, "skills\n\ncolonies")
        self.assertEqual(frame.names, {1: "x", 2: "y"})
        self.assertEqual(frame.idle, "no data")

    def test_orders_show_their_table_only_alone(self):
        from unittest import mock
        fake = cmd_watch.OrderPoll(books=(), warnings=(), eligible=False)
        client = SimpleNamespace(now=lambda: None)
        with mock.patch.object(cmd_watch, "poll_order_observations", return_value=fake), \
                mock.patch.object(cmd_watch, "render_orders_watch_status", return_value="TABLE"):
            alone = cmd_watch.orders_poll(SimpleNamespace(), client, WatchSession(sources=("orders",)))
            shared = cmd_watch.orders_poll(SimpleNamespace(), client, WatchSession(sources=("skills", "orders")))
        self.assertEqual(alone.body, "TABLE")
        self.assertEqual(len(alone.warnings), 1, "nobody to poll is said once, when watching alone")
        self.assertEqual((shared.body, shared.warnings), ("", ()))


class SkillDefaultsTests(unittest.TestCase):
    def test_watch_args_gain_the_skills_defaults(self):
        from newedenworks.characters import watch
        args = watch._with_skill_defaults(SimpleNamespace(watch=5, full=True, char=None))
        self.assertEqual((args.filter, args.sort, args.trained_only, args.full, args.watch), ("all", "name", False, True, 5))


if __name__ == "__main__":
    unittest.main()
