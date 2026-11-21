"""The core store's per-plugin schema versions."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from newedenworks import ledger_db, registry, store
from newedenworks.plugin import Schema

from . import legacy_ledger_schema

from .needs import TRADE

# Every schema-6 owner that is installed here: the core's, the built-in plugins', and trade's when the
# private plugin is installed. Without trade a file cannot be complete (user_version stays below 6).
ALL = [ledger_db.CORE_SCHEMA, *registry.load(registry.builtin_candidates(), enabled=None).schemas()]
if TRADE:
    from newedenworks_trade import SCHEMA as TRADE_SCHEMA
    ALL.append(TRADE_SCHEMA)
LEDGER = next(s for s in ALL if s.name == "ledger")
INSTALLED = {s.name for s in ALL}
TRADE_OBJECTS = ("market_history", "book_snapshots", "watchlist")


def shape(conn: sqlite3.Connection) -> dict[str, str]:
    """Every schema object except the store's own bookkeeping, by name, with its stored SQL."""
    return {name: sql for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE name NOT IN ('plugin_schema', 'sqlite_autoindex_plugin_schema_1')")}


def schema6(conn: sqlite3.Connection) -> dict[str, str]:
    """What eve-skills schema 6 holds of the owners installed here (all of it when trade is)."""
    objects = shape(conn)
    if TRADE:
        return objects
    return {name: sql for name, sql in objects.items() if not any(t in name for t in TRADE_OBJECTS)}


COMPLETE = 6 if TRADE else None    # user_version once migrated: 6, or left as it was without trade


class StoreFixture(unittest.TestCase):
    """StoreTests's fake ESI and helpers, without tests: the trade plugin's tests derive from it too."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def conn(self, name="ledger.sqlite3") -> sqlite3.Connection:
        conn = sqlite3.connect(os.path.join(self.tmp.name, name))
        self.addCleanup(conn.close)
        return conn

    def legacy(self, version: int, name: str = "old.sqlite3") -> sqlite3.Connection:
        conn = self.conn(name)
        legacy_ledger_schema.build(conn, version)
        return conn

    def user_version(self, conn) -> int:
        return conn.execute("PRAGMA user_version").fetchone()[0]


class StoreTests(StoreFixture):
    def test_a_fresh_file_matches_eve_skills_schema_6_exactly(self):
        fresh = self.conn()
        store.migrate(fresh, ALL)
        self.assertEqual(shape(fresh), schema6(self.legacy(6)))
        self.assertEqual(self.user_version(fresh), COMPLETE or 0)
        self.assertEqual(store.versions(fresh), {name: 1 for name in store.LEGACY_OWNERS if name in INSTALLED})

    def test_a_schema_6_file_is_adopted_without_running_any_sql(self):
        conn = self.legacy(6)
        with conn:
            conn.execute("INSERT INTO notes (ts, kind, title) VALUES ('2026-11-21T00:00:00Z', 'update', 'kept')")
        before = shape(conn)
        executed = []
        conn.set_trace_callback(executed.append)
        store.migrate(conn, ALL)
        conn.set_trace_callback(None)
        self.assertEqual(shape(conn), before)
        self.assertEqual(store.versions(conn), {name: 1 for name in store.LEGACY_OWNERS})
        self.assertFalse([sql for sql in executed if "CREATE TABLE IF NOT EXISTS" in sql and "plugin_schema" not in sql])
        self.assertEqual(conn.execute("SELECT title FROM notes").fetchall(), [("kept",)])
        self.assertEqual(self.user_version(conn), 6)

    def test_every_older_version_is_completed_to_schema_6(self):
        reference = schema6(self.legacy(6, "reference.sqlite3"))
        for version in range(1, 6):
            with self.subTest(version=version):
                conn = self.legacy(version, f"v{version}.sqlite3")
                with conn:
                    conn.execute("INSERT INTO meta (key, value) VALUES ('cutover', '2026-09-04')")
                store.migrate(conn, ALL)
                self.assertEqual(schema6(conn), reference)
                self.assertEqual(self.user_version(conn), COMPLETE or version)
                self.assertEqual(conn.execute("SELECT value FROM meta").fetchall(), [("2026-09-04",)])

    def test_migrating_twice_changes_nothing(self):
        conn = self.conn()
        store.migrate(conn, ALL)
        before = (shape(conn), store.versions(conn))
        store.migrate(conn, ALL)
        self.assertEqual((shape(conn), store.versions(conn)), before)

    def test_a_subset_of_plugins_creates_only_their_tables_and_the_rest_later(self):
        conn = self.conn()
        store.migrate(conn, [ledger_db.CORE_SCHEMA, LEDGER])    # core + ledger only
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn("transactions", tables)
        self.assertNotIn("jobs", tables)
        self.assertEqual(self.user_version(conn), 0, "not a complete schema-6 file yet")
        store.migrate(conn, ALL)
        self.assertEqual(shape(conn), schema6(self.legacy(6)))
        self.assertEqual(self.user_version(conn), COMPLETE or 0)

    def test_new_versions_run_once_in_order_and_may_be_functions(self):
        conn = self.conn()
        store.migrate(conn, ALL)
        ran = []

        def backfill(c):
            ran.append("v3")
            c.execute("INSERT INTO extra (n) VALUES (1)")

        grown = Schema("ledger", (LEDGER.migrations[0], "CREATE TABLE IF NOT EXISTS extra (n INTEGER);", backfill),
                       LEDGER.tables + ("extra",))
        rest = [s for s in ALL if s.name not in ("core", "ledger")]
        store.migrate(conn, [ledger_db.CORE_SCHEMA, grown, *rest])
        store.migrate(conn, [ledger_db.CORE_SCHEMA, grown, *rest])
        self.assertEqual(ran, ["v3"])
        self.assertEqual(store.versions(conn)["ledger"], 3)
        self.assertEqual(conn.execute("SELECT n FROM extra").fetchall(), [(1,)])

    def test_tables_from_a_newer_plugin_version_are_refused(self):
        conn = self.conn()
        store.migrate(conn, ALL)
        with conn:
            conn.execute("UPDATE plugin_schema SET version = 2 WHERE plugin = 'industry'")
        with self.assertRaisesRegex(RuntimeError, "industry tables .* version 2"):
            store.migrate(conn, ALL)

    def test_a_file_stamped_past_schema_6_is_refused(self):
        conn = self.conn()
        conn.execute("PRAGMA user_version = 7")
        with self.assertRaisesRegex(RuntimeError, "written by a newer version"):
            store.migrate(conn, ALL)

    def test_an_uninstalled_plugins_version_is_left_alone(self):
        conn = self.conn()
        store.migrate(conn, ALL)
        store.migrate(conn, [s for s in ALL if s.name != "planetary"])
        self.assertEqual(store.versions(conn)["planetary"], 1)
        self.assertIn("pi_polls", shape(conn))

    def test_ledger_connect_runs_the_loaded_plugins_schemas(self):
        path = os.path.join(self.tmp.name, "via-connect.sqlite3")
        conn = ledger_db.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(store.versions(conn), {s.name: 1 for s in store.schemas()})
        self.assertEqual(shape(conn), schema6(self.legacy(6)))

    def test_versions_never_creates_the_table(self):
        conn = self.conn()
        self.assertEqual(store.versions(conn), {})
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'plugin_schema'").fetchone())


if __name__ == "__main__":
    unittest.main()
