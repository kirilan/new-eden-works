"""`neworks migrate`: copy the `eve-skills` folders to their `new-eden-works` names.

Until it runs, every kind of folder (config, data, state, cache) still resolves to its `eve-skills`
one (`paths`), so an installation keeps working untouched. `migrate` shows what it would copy;
`migrate --apply` copies, and from then on the new folders are the ones used. The old folders are
left exactly as they were, as a backup nobody reads any more.

Each kind is copied into a `<name>.migrating` folder beside its destination and renamed into place
only when complete, so no command ever sees half a folder. SQLite files (the ledger and its backups)
are copied through SQLite's backup API, which carries what is still in the write-ahead log; lock
files are left behind. The copy refuses while a service job holds its lock: a job writing to the old
folders during the copy would lose that write.

The alert settings move too: an `alerts.json` in the config folder in use becomes config.json's
`notify` section and the sources' keys in `industry` and `trade.alerts` (`notify`), checked to give
the very same settings before config.json is written, and the file is renamed `alerts.json.migrated`.
That happens in the new folder only, after the copy; the old folder keeps its alerts.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sqlite3

from . import notify, paths, storage
from .plugin import Command

KINDS = ("config", "data", "state", "cache")
SQLITE_MAGIC = b"SQLite format 3\x00"
SIDECARS = ("-wal", "-shm", "-journal")     # a database's log files: its backup copy carries what they hold
PARTIAL = ".migrating"
SETTINGS_MOVED = notify.SETTINGS_NAME + ".migrated"


def plan() -> list[dict]:
    """Per kind: where it was, where it goes, and what `--apply` does with it."""
    rows = []
    for kind in KINDS:
        new, old = paths.candidates(kind)
        new_there, old_there = os.path.isdir(new), os.path.isdir(old)
        if new_there:
            action = "done"         # already migrated (or a new installation): the new folder is used
        elif old_there:
            action = "copy"
        else:
            action = "nothing"
        rows.append({"kind": kind, "old": old, "new": new, "old_exists": old_there, "new_exists": new_there,
                     "in_use": new if new_there or not old_there else old, "action": action,
                     "files": _count(old) if action == "copy" else None})
    return rows


def _count(root: str) -> int:
    return sum(1 for _ in _files(root))


def _files(root: str):
    for folder, dirs, names in os.walk(root):
        for name in _skipped(folder, names):
            names.remove(name)
        yield from (os.path.join(folder, name) for name in names)


def _skipped(folder: str, names) -> list[str]:
    """Lock files, and the log files of a database that sits beside them."""
    return [name for name in names
            if name.endswith(".lock")
            or any(name.endswith(end) and name[:-len(end)] in names
                   and _is_sqlite(os.path.join(folder, name[:-len(end)])) for end in SIDECARS)]


def busy_locks() -> list[str]:
    """The service jobs whose lock is held right now (the dashboard daemon holds its own for good)."""
    held = []
    for kind_dir in {*paths.candidates("state")}:
        for lock in sorted(glob.glob(os.path.join(kind_dir, "service", "*.lock"))):
            if storage.lock_held(lock):
                held.append(lock)
    return held


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def _copy_file(src: str, dst: str) -> str:
    # A database with a write-ahead log beside it is copied through SQLite, which reads the log too;
    # any other file, a database at rest included, byte for byte, so the old folder is never touched.
    if os.path.exists(src + "-wal") and _is_sqlite(src):
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(dst)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        shutil.copystat(src, dst)
        return dst
    return shutil.copy2(src, dst)


def copy_kind(old: str, new: str) -> int:
    """Copy one folder to its new name through a partial folder; the number of files copied."""
    partial = new + PARTIAL
    if os.path.exists(partial):
        shutil.rmtree(partial)          # an earlier copy that did not finish
    os.makedirs(os.path.dirname(new), exist_ok=True)
    shutil.copytree(old, partial, symlinks=True, ignore=_skipped, copy_function=_copy_file)
    copied = _count(partial)
    if copied != _count(old):
        shutil.rmtree(partial)
        raise RuntimeError(f"copying {old} gave {copied} files, not {_count(old)}; nothing was changed")
    os.replace(partial, new)
    return copied


def apply(rows: list[dict]) -> list[dict]:
    busy = busy_locks()
    if busy:
        raise RuntimeError("a service job is running (" + ", ".join(os.path.basename(b)[:-5] for b in busy)
                           + "): stop the service first (systemctl --user stop new-eden-works.service), "
                             "and any `watch`, then run migrate --apply again")
    done = []
    for row in rows:
        if row["action"] == "copy":
            row = {**row, "files": copy_kind(row["old"], row["new"]), "action": "copied", "in_use": row["new"]}
        done.append(row)
    return done


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    except ValueError as err:
        raise RuntimeError(f"{path}: not valid JSON ({err}); fix it before migrating") from None
    return doc if isinstance(doc, dict) else {}


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def settings_plan(config_dir: str) -> dict | None:
    """What becomes of alerts.json in this config folder; None when there is none to move (or
    config.json already has its notify section, which `doctor` reports)."""
    path = os.path.join(config_dir, notify.SETTINGS_NAME)
    if not os.path.exists(path) or "notify" in _read_json(os.path.join(config_dir, "config.json")):
        return None
    sections, industry, trade, left_out = notify.split_doc(notify.read_legacy(path))
    return {"from": path, "to": os.path.join(config_dir, "config.json"), "notify": sections,
            "industry": industry, "trade_alerts": trade, "left_out": left_out, "action": "split"}


def split_settings(config_dir: str) -> dict | None:
    """Move alerts.json into config.json's sections, refusing if that would change a single setting."""
    row = settings_plan(config_dir)
    if row is None:
        return None
    with storage.file_lock(os.path.join(config_dir, "config.lock")):
        raw = _read_json(row["to"])
        cfg = {**raw, "notify": row["notify"]}
        if row["industry"]:
            cfg["industry"] = {**_object(raw.get("industry")), **row["industry"]}
        if row["trade_alerts"]:
            trade = _object(raw.get("trade"))
            cfg["trade"] = {**trade, "alerts": {**_object(trade.get("alerts")), **row["trade_alerts"]}}
        before = notify.parse_settings(notify.read_legacy(row["from"]))
        after = notify.parse_settings(notify.sections_doc(cfg), legacy=False)
        if after != before:
            raise RuntimeError(f"moving {row['from']} into config.json would change the alert settings "
                               f"({before} became {after}); nothing was changed")
        storage.atomic_write_json(row["to"], cfg, private=True)
    os.replace(row["from"], os.path.join(config_dir, SETTINGS_MOVED))
    return {**row, "action": "split done"}


def migrate_data(args, ctx) -> dict:
    rows = plan()
    settings = settings_plan(paths.config_dir(create=False))
    if args.apply:
        rows = apply(rows)
        config_dir = paths.config_dir(create=False)
        # Only ever in the new folder: the old one stays exactly as it was.
        settings = split_settings(config_dir) if config_dir != paths.candidates("config")[1] else None
    return {"applied": bool(args.apply), "folders": rows, "settings": settings}


def migrate_text(data: dict, args) -> str:
    lines = []
    for row in data["folders"]:
        action = row["action"]
        if action == "copy":
            what = f"copy {row['files']} files from {row['old']}"
        elif action == "copied":
            what = f"copied {row['files']} files from {row['old']} (kept as it was, as a backup)"
        elif action == "done":
            what = "already in use" + (f"; {row['old']} is no longer read" if row["old_exists"] else "")
        else:
            what = "nothing to copy (created when first needed)"
        lines.append(f"{row['kind']:<7} {row['new']}: {what}")
    settings = data.get("settings")
    if settings:
        parts = [f"notify ({', '.join(settings['notify']) or 'defaults'})"]
        if settings["industry"]:
            parts.append(f"industry ({', '.join(settings['industry'])})")
        if settings["trade_alerts"]:
            parts.append(f"trade.alerts ({', '.join(settings['trade_alerts'])})")
        verb = "moved" if settings["action"] == "split done" else "move"
        lines.append(f"alerts  {verb} alerts.json into config.json: {', '.join(parts)}"
                     + (f"; renamed {SETTINGS_MOVED}" if verb == "moved" else ""))
        if settings["left_out"]:
            lines.append(f"        left out (never read): {', '.join(settings['left_out'])}")
    pending = any(row["action"] == "copy" for row in data["folders"]) or (settings or {}).get("action") == "split"
    if pending:
        lines.append("")
        lines.append("Nothing was changed. Stop the service and any watch, run `neworks migrate --apply`, then start "
                     "the service again.")
    elif data["applied"] and any(row["action"] == "copied" for row in data["folders"]):
        lines.append("")
        lines.append("Every command now uses the new-eden-works folders. Start the service again "
                     "(systemctl --user start new-eden-works.service).")
    elif data["applied"] and settings:
        lines.append("")
        lines.append("The alert settings are now read from config.json.")
    return "\n".join(lines)


def _migrate_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--apply", action="store_true",
                   help="copy the folders and move the alert settings (without it: only show what "
                        "would be done)")


COMMAND = Command(("migrate",), help="copy the eve-skills config, data, state and cache folders to their "
                                     "new-eden-works names (the old ones stay, as a backup) and move "
                                     "alerts.json into config.json",
                  configure=_migrate_args, run=migrate_data, render=migrate_text)
