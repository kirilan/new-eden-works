"""The ledger plugin's watch source: `--ledger` on a watch syncs the ledger at most once an hour, so
an overnight watch never lets ESI's 30-day wallet window pass unsynced."""

from __future__ import annotations

from .. import esi as esi_mod
from ..cmd_watch import WatchCycle, WatchSession, warn_once
from . import ledger_sync


def ledger_wanted(args) -> bool:
    return bool(getattr(args, "ledger", False))


def ledger_poll(args, client: esi_mod.Esi, session: WatchSession) -> WatchCycle:
    status, problems = ledger_sync.sync_if_due(client)
    warnings = [w for line in problems for w in warn_once(session.warned, f"ledger:{line}", line)]
    return WatchCycle(warnings=tuple(warnings), body=status or "")
