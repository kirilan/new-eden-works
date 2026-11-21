"""The core's notify service: alert sources, the shared reminder clock and the one message per run."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from newedenworks import notify, registry
from newedenworks.industry import alert_source as jobs_source
from newedenworks.plugin import API_VERSION, AlertSource, Plugin

UTC = timezone.utc
NOON = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
SETTINGS = notify.parse_settings({"remind_hours": 4, "quiet_hours": "00:00-08:00", "timezone": "+02:00"})


def batch(fresh=False, standing=()):
    return notify.Batch(render=None, fresh=fresh, standing=list(standing))


class ComposeTests(unittest.TestCase):
    def test_one_source_names_the_title_and_ranks_order_the_rest(self):
        orders = notify.Parts("orders", True, heads=[(10, "2 behind"), (30, "3 behind")],
                              blocks=[(10, "news"), (40, "still"), (50, "also")])
        jobs = notify.Parts("jobs", True, heads=[(20, "1 job ending")], blocks=[(20, "soon"), (30, "waiting")])
        self.assertEqual(notify.compose([orders]), "EVE orders: 2 behind, 3 behind\n\nnews\n\nstill\n\nalso")
        self.assertEqual(notify.compose([orders, jobs]),
                         "EVE: 2 behind, 1 job ending, 3 behind\n\nnews\n\nsoon\n\nwaiting\n\nstill\n\nalso")
        quiet = notify.Parts("orders", False, heads=[(10, "x")])
        self.assertEqual(notify.compose([quiet, jobs]), "EVE jobs: 1 job ending\n\nsoon\n\nwaiting")

    def test_messages_of_their_own_follow_and_nothing_is_none(self):
        summary = notify.Parts("jobs", False, after=["EVE jobs: 08:00-10:00 window"])
        self.assertEqual(notify.compose([summary]), "EVE jobs: 08:00-10:00 window")
        self.assertIsNone(notify.compose([notify.Parts("orders"), notify.Parts("jobs")]))

    def test_log_line(self):
        parts = [notify.Parts("orders", counts="1 behind"), notify.Parts("jobs", counts="0 jobs ending", log_tail="; 2 held")]
        batches = [notify.Batch(render=None, log_head="5 open orders"), notify.Batch(render=None)]
        self.assertEqual(notify.log_line("T", batches, parts, "text", True), "T 5 open orders; sent: 1 behind, 0 jobs ending (silent); 2 held")
        self.assertEqual(notify.log_line("T", batches[1:], parts[1:], None, False), "T nothing new; 2 held")


class ReminderTests(unittest.TestCase):
    def test_one_clock_from_the_oldest_standing_item(self):
        start = NOON.timestamp()
        state, due = notify.reminder_due({"last_reminder": None}, [batch(standing=[start]), batch()], SETTINGS,
                                         NOON + timedelta(hours=2))
        self.assertEqual((state["last_reminder"], due), (None, False))
        state, due = notify.reminder_due(state, [batch(standing=[start]), batch(standing=[start + 3600])], SETTINGS,
                                         NOON + timedelta(hours=4))
        self.assertTrue(due)
        self.assertEqual(state["last_reminder"], (NOON + timedelta(hours=4)).timestamp())

    def test_never_in_quiet_hours_and_reset_when_nothing_stands(self):
        night = datetime(2026, 11, 11, 23, 0, tzinfo=UTC)          # 01:00 at +02:00
        state, due = notify.reminder_due({"last_reminder": None}, [batch(standing=[0.0])], SETTINGS, night)
        self.assertEqual((state["last_reminder"], due), (None, False))
        state, _ = notify.reminder_due({"last_reminder": 5.0}, [batch(fresh=True)], SETTINGS, NOON)
        self.assertEqual(state["last_reminder"], 5.0)              # fresh news keeps the clock
        state, _ = notify.reminder_due({"last_reminder": 5.0}, [batch()], SETTINGS, NOON)
        self.assertIsNone(state["last_reminder"])

    def test_remind_hours_zero_never_reminds(self):
        settings = notify.parse_settings({"remind_hours": 0})
        _, due = notify.reminder_due({"last_reminder": None}, [batch(standing=[0.0])], settings, NOON)
        self.assertFalse(due)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = os.path.join(self.tmp.name, "eve-skills")
        os.makedirs(self.dir)

    def write(self, name, doc):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def test_the_clock_and_announced_jobs_come_from_the_order_state_once(self):
        self.write("order-alerts.json", {"orders": {}, "pending": {}, "jobs": {"7": {"announced_at": 1.0}},
                                         "last_reminder": 42.0})
        self.assertEqual(notify.load_state(), {"last_reminder": 42.0})
        self.assertEqual(jobs_source.load_state()["announced"], {"7": {"announced_at": 1.0}})
        notify.save_state({"last_reminder": None})
        self.write("job-alerts.json", {"held": {}, "last_window": None, "announced": {}})
        self.assertEqual(notify.load_state(), {"last_reminder": None})
        self.assertEqual(jobs_source.load_state()["announced"], {})

    def test_nothing_stored(self):
        self.assertEqual(notify.load_state(), {"last_reminder": None})
        self.assertEqual(jobs_source.load_state()["announced"], {})


class SettingsSourceTests(unittest.TestCase):
    """Where the settings are read: config.json's sections, or alerts.json until there is a notify section."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "EVE_SKILLS_CLIENT_ID": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = os.path.join(self.tmp.name, "new-eden-works")
        os.makedirs(self.dir)

    def write(self, name, doc):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def test_nothing_written_gives_the_defaults_and_writes_nothing(self):
        self.assertEqual(notify.load_settings(), notify.parse_settings({}))
        self.assertEqual(os.listdir(self.dir), [])

    def test_alerts_json_is_read_until_config_json_has_a_notify_section(self):
        self.write("alerts.json", {"timezone": "+02:00", "remind_hours": 2})
        self.write("config.json", {"industry": {"job_notice_minutes": 5}})
        self.assertEqual(notify.load_settings().remind_hours, 2)
        self.assertEqual(notify.load_settings().job_notice_minutes, 20)       # alerts.json alone counts
        self.write("config.json", {"notify": {"remind_hours": 6}, "industry": {"job_notice_minutes": 5},
                                   "trade": {"alerts": {"min_gap_pct": 1}}})
        settings = notify.load_settings()
        self.assertEqual((settings.remind_hours, settings.job_notice_minutes, settings.min_gap_pct, settings.timezone),
                         (6, 5, 1, "UTC"))
        self.assertEqual(notify.problems(notify.sso.load_config()),
                         [("notify", f"{notify.settings_path()} is no longer read (config.json has a notify "
                                     "section): delete it, or rename it to keep it")])

    def test_a_bad_value_names_its_section(self):
        self.write("config.json", {"notify": {"bogus": 1}, "industry": {"jobs": {"windows": 7}}})
        with self.assertRaisesRegex(RuntimeError, r"^config\.json: industry\.jobs\.windows must be a string"):
            notify.load_settings()
        found = notify.problems(notify.sso.load_config())
        self.assertIn(("industry", "config.json: industry.jobs.windows must be a string such as \"08-10,20-22\" "
                                   "(or null)"), found)
        self.assertIn(("notify", "notify: unknown key 'bogus' (known: exclude_locations, exclude_characters, "
                                 "remind_hours, quiet_hours, timezone)"), found)
        self.write("config.json", {"trade": {"alerts": {"min_gap_pct": -1}}})
        self.assertEqual(notify.problems(notify.sso.load_config()),
                         [("trade", "config.json: trade.alerts.min_gap_pct must be 0 or more")])

    def test_setup_writes_a_notify_section_to_edit(self):
        with mock.patch.object(notify.telegram, "load_config", return_value={"token": "t", "chat_id": 1}), \
                mock.patch.object(notify.telegram, "bot_name", return_value="bot"), \
                mock.patch.object(notify.telegram, "save_config"), \
                mock.patch.object(notify.telegram, "send"), \
                mock.patch("sys.stdout"):
            notify.cmd_setup(mock.Mock(token_file=None, chat_id=1))
        with open(os.path.join(self.dir, "config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["notify"]["quiet_hours"], "00:00-08:00")


class RegistryTests(unittest.TestCase):
    def test_sources_in_their_order(self):
        def source(name, plugin):
            return registry.Candidate("entry point (t 1.0)", f"{plugin}:plugin", lambda: lambda: Plugin(
                name=plugin, version="1", api=API_VERSION, alerts=(AlertSource(name, collect=print),)))
        loaded = registry.load([*registry.builtin_candidates(), source("zz", "extra"), source("orders", "market")],
                               enabled=None)
        self.assertEqual([s.name for s in notify.sources(loaded)], ["orders", "jobs", "zz"])
        clash = registry.load([*registry.builtin_candidates(), source("jobs", "other")], enabled=None)
        self.assertIsNone(clash.plugin("other"))
        self.assertIn("alert source jobs is already declared by industry", clash.refused[0].reason)

    def test_alerts_is_a_core_command(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=None)
        self.assertEqual(loaded.provider(("alerts",)), "core")
        self.assertEqual([s.name for s, p in loaded.alert_sources()], ["jobs"])


if __name__ == "__main__":
    unittest.main()
