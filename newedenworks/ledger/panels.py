"""The ledger's dashboard panels: profitability (`ledger.profit`), the colonies as their own operation
(`ledger.planetary`) and the stockpile against sales and job use (`ledger.stockpile`).

One ledger replay serves all three in a build. The stockpile reads the live inventory (`ledger
inventory`), the blueprints from industry's status, and Jita's traded volume from the trade plugin's
history table when it exists; the server records each live inventory as a stock snapshot."""

from __future__ import annotations

import contextlib
import statistics
from datetime import timedelta

from .. import dashboard, ledger_db, market, store
from ..panels import PAGE
from ..plugin import Panel
from . import cmd_ledger, stockpile

NO_LIVE = "live reports off (--no-live)"
INVENTORY = ("ledger", "inventory")


def _names(ctx):
    return lambda ids: cmd_ledger.place_names(ctx.client, ids)


def state(ctx):
    """(book, header, first day) of this build's ledger replay, done once."""
    def replay():
        with contextlib.closing(cmd_ledger._open()) as conn:
            book, header, _recipes, _invention = cmd_ledger._state(conn)
        start = ctx.since or header["cutover"] or ctx.now.date().isoformat()
        return book, header, start
    return ctx.once("ledger.state", replay)


def profit_data(ctx) -> dict:
    book, header, start = state(ctx)
    return dashboard.profit_fragment(book, header, since=start, today=ctx.now.date(), names=_names(ctx))


def planetary_data(ctx) -> dict:
    book, _header, start = state(ctx)
    # The planetary panel counts from the first planetary charge or poll, never from before `start`.
    with contextlib.closing(cmd_ledger._open()) as conn:
        planetary = {key: cmd_ledger.planetary_document(conn, book, ctx.client if ctx.live else None, day)
                     for key, day in dashboard.range_starts(ctx.now.date(), start).items()}
    return dashboard.planetary_fragment(book, since=start, today=ctx.now.date(), planetary=planetary)


def _market_volume(conn, type_ids) -> dict[int, float]:
    """Median daily units traded in Jita's region over the last 30 stored days, per type that has history.

    The history is the trade plugin's table: without it there is none, and the column stays empty."""
    out = {}
    if not store.has_table(conn, "market_history"):
        return out
    for type_id in type_ids:
        rows = ledger_db.market_history(conn, market.HUBS["jita"].region_id, type_id)[-30:]
        volumes = [r["volume"] for r in rows if r.get("volume") is not None]
        if volumes:
            out[type_id] = float(statistics.median(volumes))
    return out


def stockpile_prefetch(ctx) -> None:
    from ..industry import panels as industry_panels
    if ctx.live:
        ctx.start(*INVENTORY)
    industry_panels.prefetch_status(ctx)


def stockpile_data(ctx) -> dict:
    from ..industry import panels as industry_panels
    book, _header, _start = state(ctx)
    if ctx.live:
        inventory, inventory_error = ctx.report(*INVENTORY)
    else:
        inventory, inventory_error = None, NO_LIVE
    status, _error = industry_panels.status(ctx)
    items = (inventory or {}).get("items") or []
    now = ctx.now
    with contextlib.closing(ledger_db.connect()) as conn:
        if ctx.record and items:
            with conn:
                ledger_db.insert_stock_snapshot(conn, now.strftime("%Y-%m-%dT%H:%M:%SZ"), stockpile.stock_rows(items))
        snapshots = ledger_db.stock_snapshots(
            conn, since=(now.date() - timedelta(days=stockpile.HISTORY_DAYS - 1)).isoformat())
        volume = _market_volume(conn, book.produced_products)
    stock = stockpile.analyse(book, items, today=now.date(), market_volume=volume,
                              blueprints=[bp for owner in (status or {}).get("owners") or []
                                          for bp in owner.get("blueprints") or []],
                              snapshots=snapshots)
    stock["error"] = inventory_error
    stock["as_of"] = (inventory or {}).get("prices_as_of")
    return dashboard.stock_fragment(stock, _names(ctx))


PANELS = (
    Panel("ledger.profit", "Industry profitability", PAGE, data=profit_data),
    Panel("ledger.planetary", "Planetary industry", PAGE, data=planetary_data),
    Panel("ledger.stockpile", "Stockpile", PAGE, data=stockpile_data, prefetch=stockpile_prefetch),
)
