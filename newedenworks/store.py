"""The core store: one SQLite file shared by every plugin, with per-plugin schema versions.

The file stays the one `ledger.sqlite3` (cross-plugin questions such as `ledger sql` and the
dashboard read several plugins' tables at once). Each plugin declares a `Schema` per owner: its
tables and an ordered list of migrations. `migrate` runs what is missing at open time, core first,
then the plugins in load order, and records each owner's version in `plugin_schema`.

A file written before per-plugin versions carries only `PRAGMA user_version`. One at version 6 (the
last single version) is adopted as is: every owner's version 1 *is* schema 6, so the versions are
recorded and no table is touched. A file at version 1 to 5 gets each owner's version 1 run, which
creates only the tables it lacks. user_version stays 6 once every schema-6 owner is recorded, so an
older eve-skills still opens the file; a file stamped higher was written by something newer.

Uninstalling a plugin leaves its tables and its recorded version alone.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from .plugin import Schema

# The last single PRAGMA user_version eve-skills used (ledger_db.SCHEMA_VERSION).
LEGACY_VERSION = 6

# The owners that together made up schema 6, so each one's version 1.
LEGACY_OWNERS = ("core", "ledger", "industry", "planetary", "trade")

VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS plugin_schema (
    plugin  TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
"""


def schemas() -> list[Schema]:
    """The schemas to migrate: the core's, then every loaded plugin's in load order."""
    from . import ledger_db, registry
    return [ledger_db.CORE_SCHEMA, *registry.current().schemas()]


def versions(conn: sqlite3.Connection) -> dict[str, int]:
    """Each owner's recorded version; {} for a file without plugin_schema (never creates it)."""
    if not has_table(conn, "plugin_schema"):
        return {}
    return {row[0]: row[1] for row in conn.execute("SELECT plugin, version FROM plugin_schema")}


def behind(conn: sqlite3.Connection, owners: Iterable[Schema]) -> list[str]:
    """The owners whose tables in this file are older than this code expects (read-only: changes nothing).

    A file from before per-plugin versions is judged by its user_version: at 6 every schema-6 owner is
    at version 1, below that nothing is."""
    recorded = versions(conn)
    if not recorded:
        legacy = conn.execute("PRAGMA user_version").fetchone()[0]
        if legacy >= LEGACY_VERSION:
            recorded = {name: 1 for name in LEGACY_OWNERS}
    return [schema.name for schema in owners if recorded.get(schema.name, 0) < schema.version]


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    """Whether the file has this table: another plugin's table may not exist when that plugin never ran."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _record(conn: sqlite3.Connection, name: str, version: int) -> None:
    conn.execute("INSERT INTO plugin_schema (plugin, version) VALUES (?, ?) "
                 "ON CONFLICT (plugin) DO UPDATE SET version = excluded.version", (name, version))


def _apply(conn: sqlite3.Connection, step) -> None:
    if isinstance(step, str):
        conn.executescript(step)
    else:
        step(conn)


def migrate(conn: sqlite3.Connection, owners: Iterable[Schema], path: str = "the store") -> None:
    """Bring every owner in `owners` up to its newest version; refuse a file from a newer version."""
    owners = list(owners)
    legacy = conn.execute("PRAGMA user_version").fetchone()[0]
    if legacy > LEGACY_VERSION:
        raise RuntimeError(f"the ledger at {path} was written by a newer version "
                           f"(schema {legacy}, this one knows {LEGACY_VERSION}) - upgrade new-eden-works")
    with conn:
        conn.executescript(VERSIONS_TABLE)
    recorded = versions(conn)
    by_name = {schema.name: schema for schema in owners}

    if not recorded and legacy >= 1:
        # A file from before per-plugin versions. Schema 6 is every legacy owner's version 1; an
        # older file gets the version 1 of each owner installed here, which adds only missing tables.
        for name in LEGACY_OWNERS:
            if legacy < LEGACY_VERSION:
                if name not in by_name:
                    continue      # recorded later, when its plugin is installed and runs version 1
                with conn:
                    _apply(conn, by_name[name].migrations[0])
            with conn:
                _record(conn, name, 1)
        recorded = versions(conn)

    for schema in owners:
        current = recorded.get(schema.name, 0)
        if current > schema.version:
            raise RuntimeError(f"the {schema.name} tables in {path} are at version {current}, but this "
                               f"{schema.name} plugin knows {schema.version} - upgrade it")
        for version in range(current + 1, schema.version + 1):
            with conn:
                _apply(conn, schema.migrations[version - 1])
                _record(conn, schema.name, version)
        recorded[schema.name] = max(current, schema.version)

    if legacy < LEGACY_VERSION and all(recorded.get(name, 0) >= 1 for name in LEGACY_OWNERS):
        with conn:
            conn.execute(f"PRAGMA user_version = {LEGACY_VERSION}")
