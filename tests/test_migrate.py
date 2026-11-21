"""`neworks migrate` and the folder names: eve-skills folders stay in use until copied, then the new ones."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from newedenworks import migrate, paths, storage

XDG = {"config": "XDG_CONFIG_HOME", "cache": "XDG_CACHE_HOME", "data": "XDG_DATA_HOME", "state": "XDG_STATE_HOME"}


class _Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="neworks-migrate-")
        self.addCleanup(self.tmp.cleanup)
        self.roots = {kind: os.path.join(self.tmp.name, kind) for kind in XDG}
        patcher = mock.patch.dict(os.environ, {var: self.roots[kind] for kind, var in XDG.items()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def old(self, kind, *parts):
        return os.path.join(self.roots[kind], "eve-skills", *parts)

    def new(self, kind, *parts):
        return os.path.join(self.roots[kind], "new-eden-works", *parts)

    def write(self, path, text="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class ResolveTest(_Folders):
    def test_a_new_installation_gets_the_new_names(self):
        self.assertEqual(paths.config_dir(create=False), self.new("config"))
        self.assertEqual(paths.state_dir(create=True), self.new("state"))
        self.assertTrue(os.path.isdir(self.new("state")))
        self.assertFalse(os.path.exists(self.old("state")))

    def test_an_old_folder_stays_in_use_until_the_new_one_exists(self):
        os.makedirs(self.old("config"))
        os.makedirs(self.new("data"))
        os.makedirs(self.old("data"))
        self.assertEqual(paths.config_dir(), self.old("config"))         # create=True never makes the new one
        self.assertFalse(os.path.exists(self.new("config")))
        self.assertEqual(paths.data_dir(create=False), self.new("data"))  # both there: the new one wins
        self.assertEqual(paths.cache_dir(create=False), self.new("cache"))  # each kind on its own

    def test_windows_falls_back_the_same_way(self):
        local = os.path.join(self.tmp.name, "Local")
        os.makedirs(os.path.join(local, "eve-skills", "config"))
        with mock.patch.object(paths, "is_windows", return_value=True), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": local}):
            for var in XDG.values():
                os.environ.pop(var)
            self.assertEqual(paths.config_dir(create=False), os.path.join(local, "eve-skills", "config"))
            self.assertEqual(paths.state_dir(create=False), os.path.join(local, "new-eden-works", "state"))


class MigrateTest(_Folders):
    def seed(self):
        self.write(self.old("config", "config.json"), json.dumps({"client_id": "abc"}))
        self.write(self.old("config", "playbook", "ops-check.md"), "rules")
        self.write(self.old("config", "tokens.lock"), "")
        self.write(self.old("state", "service", "alerts.json"), "{}")
        self.write(self.old("state", "service", "alerts.lock"), "")
        os.makedirs(self.old("data"))
        # A ledger in WAL mode with a row still in the log, as a running sync leaves it.
        self.db = sqlite3.connect(self.old("data", "ledger.sqlite3"))
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.execute("CREATE TABLE t (v INTEGER)")
        self.db.execute("INSERT INTO t VALUES (42)")
        self.db.commit()
        self.assertTrue(os.path.exists(self.old("data", "ledger.sqlite3-wal")))
        # A backup at rest, also in WAL mode: opening it would leave a log beside it in the old folder.
        rest = sqlite3.connect(self.old("data", "ledger.sqlite3.bak-1"))
        rest.execute("PRAGMA journal_mode=WAL")
        rest.execute("CREATE TABLE t (v INTEGER)")
        rest.commit()
        rest.close()
        self.assertFalse(os.path.exists(self.old("data", "ledger.sqlite3.bak-1-wal")))

    def listing(self, root):
        return sorted(os.path.relpath(os.path.join(folder, name), root)
                      for folder, _dirs, names in os.walk(root) for name in names)

    def run_command(self, apply):
        data = migrate.migrate_data(SimpleNamespace(apply=apply), None)
        return data, migrate.migrate_text(data, SimpleNamespace())

    def test_without_apply_nothing_is_copied(self):
        self.seed()
        data, text = self.run_command(apply=False)
        rows = {row["kind"]: row for row in data["folders"]}
        self.assertEqual(rows["config"]["action"], "copy")
        self.assertEqual(rows["config"]["files"], 2)                  # the lock file stays behind
        self.assertEqual(rows["cache"]["action"], "nothing")
        self.assertEqual(rows["config"]["in_use"], self.old("config"))
        self.assertIn("Nothing was changed", text)
        self.assertFalse(os.path.exists(self.new("config")))

    def test_apply_copies_every_kind_and_keeps_the_old_folders(self):
        self.seed()
        before = {kind: self.listing(self.old(kind)) for kind in ("config", "data", "state")}
        data, text = self.run_command(apply=True)
        self.assertEqual({kind: self.listing(self.old(kind)) for kind in before}, before)   # untouched
        self.assertEqual(self.listing(self.new("data")), ["ledger.sqlite3", "ledger.sqlite3.bak-1"])
        self.assertEqual({row["kind"]: row["action"] for row in data["folders"]},
                         {"config": "copied", "data": "copied", "state": "copied", "cache": "nothing"})
        self.assertIn("now uses the new-eden-works folders", text)
        self.assertEqual(paths.config_dir(create=False), self.new("config"))
        with open(self.new("config", "playbook", "ops-check.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "rules")
        self.assertFalse(os.path.exists(self.new("config", "tokens.lock")))
        self.assertFalse(os.path.exists(self.new("state", "service", "alerts.lock")))
        copy = sqlite3.connect(self.new("data", "ledger.sqlite3"))
        try:
            self.assertEqual(copy.execute("SELECT v FROM t").fetchall(), [(42,)])
        finally:
            copy.close()
        self.assertTrue(os.path.exists(self.old("config", "config.json")))        # the backup
        self.assertFalse(any(name.endswith(migrate.PARTIAL) for name in os.listdir(self.roots["config"])))
        again, text = self.run_command(apply=True)                                 # a second run copies nothing
        self.assertEqual({row["action"] for row in again["folders"]}, {"done", "nothing"})
        self.assertIn("is no longer read", text)

    def test_a_leftover_partial_copy_is_replaced(self):
        self.seed()
        self.write(self.new("config") + migrate.PARTIAL + "/stale.json")
        self.run_command(apply=True)
        self.assertFalse(os.path.exists(self.new("config", "stale.json")))
        self.assertFalse(os.path.exists(self.new("config") + migrate.PARTIAL))

    def test_refuses_while_a_service_job_holds_its_lock(self):
        self.seed()
        with storage.file_lock(self.old("state", "service", "alerts.lock")):
            with self.assertRaisesRegex(RuntimeError, r"service job is running \(alerts\)"):
                self.run_command(apply=True)
        self.assertFalse(os.path.exists(self.new("config")))
        self.run_command(apply=True)                    # free again: it goes ahead
        self.assertTrue(os.path.isdir(self.new("config")))

    def test_the_command_is_registered(self):
        from newedenworks import cli
        self.seed()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(["migrate", "--json"])
        self.assertIn(rc, (0, None))
        self.assertEqual(json.loads(out.getvalue())["applied"], False)


class SettingsSplitTest(_Folders):
    ALERTS = {"exclude_locations": ["Z-EXCL"], "exclude_characters": [], "remind_hours": 4,
              "quiet_hours": "00:00-08:00", "timezone": "+01:00", "job_alerts": True,
              "job_notice_minutes": 20, "jobs": {"windows": "08-10"}, "min_gap_pct": 0.5, "old_key": 1}

    def seed(self, config=None):
        self.write(self.old("config", "config.json"), json.dumps(config or {"client_id": "abc"}))
        self.write(self.old("config", "alerts.json"), json.dumps(self.ALERTS))

    def test_apply_moves_alerts_json_into_the_sections_with_the_same_settings(self):
        from newedenworks import notify
        self.seed({"client_id": "abc", "industry": {"other": 1}, "trade": {"x": 2}})
        before = notify.load_settings()
        data = migrate.migrate_data(SimpleNamespace(apply=False), None)
        self.assertEqual(data["settings"]["action"], "split")
        self.assertIn("move alerts.json into config.json", migrate.migrate_text(data, None))
        data = migrate.migrate_data(SimpleNamespace(apply=True), None)
        text = migrate.migrate_text(data, None)
        self.assertIn("moved alerts.json into config.json: notify (", text)
        self.assertIn("left out (never read): old_key", text)
        with open(self.new("config", "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["client_id"], "abc")
        self.assertEqual(cfg["notify"]["timezone"], "+01:00")
        self.assertEqual(cfg["industry"], {"other": 1, "job_alerts": True, "job_notice_minutes": 20,
                                           "jobs": {"windows": "08-10"}})
        self.assertEqual(cfg["trade"], {"x": 2, "alerts": {"min_gap_pct": 0.5}})
        self.assertFalse(os.path.exists(self.new("config", "alerts.json")))
        self.assertTrue(os.path.exists(self.new("config", "alerts.json.migrated")))
        self.assertTrue(os.path.exists(self.old("config", "alerts.json")))          # the backup keeps it
        with open(self.old("config", "config.json"), encoding="utf-8") as fh:
            self.assertNotIn("notify", json.load(fh))
        self.assertEqual(notify.load_settings(), before)
        self.assertEqual(notify.problems(notify.sso.load_config()), [])
        again = migrate.migrate_data(SimpleNamespace(apply=True), None)
        self.assertIsNone(again["settings"])

    def test_already_on_the_new_folders_it_splits_in_place(self):
        self.write(self.new("config", "alerts.json"), json.dumps({"timezone": "+02:00"}))
        data = migrate.migrate_data(SimpleNamespace(apply=True), None)
        self.assertEqual(data["settings"]["action"], "split done")
        self.assertIn("now read from config.json", migrate.migrate_text(data, None))
        with open(self.new("config", "config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"notify": {"timezone": "+02:00"}})

    def test_a_notify_section_means_nothing_to_move(self):
        self.seed({"notify": {}})
        self.assertIsNone(migrate.migrate_data(SimpleNamespace(apply=False), None)["settings"])


class LockHeldTest(unittest.TestCase):
    def test_reports_a_held_lock_without_creating_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "job.lock")
            self.assertFalse(storage.lock_held(path))
            self.assertFalse(os.path.exists(path))
            with storage.file_lock(path):
                self.assertTrue(storage.lock_held(path))
            self.assertFalse(storage.lock_held(path))


if __name__ == "__main__":
    unittest.main()
