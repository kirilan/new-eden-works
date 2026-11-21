"""The `ledger` plugin: the accounting ledger - sync, profit and loss, products, markets, invention,
inventory, notes, progress, read-only SQL and the schema.

It requires `industry`: jobs are costed with its recipes, job reads and facility history. It enhances
`planetary`: a full sync takes a holdings poll only while that plugin is loaded, and the colonies'
accounting reads whatever polls the file holds. The `wallet` consent and the ledger tables are its.
The file itself, with its connection and row accessors, is still `ledger_db` in the core.
"""

from __future__ import annotations

from .. import __version__, ledger_db, sso
from ..plugin import API_VERSION, Command, Plugin, Schema, WatchSource
from . import cmd_ledger, ledger_sync, panels, tools, watch

# This plugin's consent feature (what `login --scopes` accepts), taken from sso.OPTIONAL_SCOPES.
FEATURES = ("wallet",)

# Wallet facts, prices, sync bookkeeping, the operation's log and stock snapshots. Version 1 is exactly
# these tables as eve-skills schema 6 had them (see the notes in ledger_db).
LEDGER_V1 = """
-- Market transactions. `wallet` is 'corp:<id>:<division>' or 'char:<id>'; the same trade between two
-- of our own wallets is two rows, which is exactly how the engine recognises an internal transfer.
CREATE TABLE IF NOT EXISTS transactions (
    wallet         TEXT NOT NULL,
    transaction_id INTEGER NOT NULL,
    date           TEXT NOT NULL,
    type_id        INTEGER NOT NULL,
    quantity       INTEGER NOT NULL,
    unit_price     REAL NOT NULL,
    is_buy         INTEGER NOT NULL,
    client_id      INTEGER,
    location_id    INTEGER,
    journal_ref_id INTEGER,
    raw            TEXT NOT NULL,
    PRIMARY KEY (wallet, transaction_id)
);

-- Wallet journal. One ref id can appear in two wallets (a transfer between them), hence the pair key.
CREATE TABLE IF NOT EXISTS journal (
    wallet          TEXT NOT NULL,
    id              INTEGER NOT NULL,
    date            TEXT NOT NULL,
    ref_type        TEXT NOT NULL,
    amount          REAL,
    context_id      INTEGER,
    context_id_type TEXT,
    first_party_id  INTEGER,
    second_party_id INTEGER,
    raw             TEXT NOT NULL,
    PRIMARY KEY (wallet, id)
);

-- Market orders, open and historical. Kept so broker fees - which the journal does not link to any
-- order - can be matched to the order whose `issued` stamp they share.
CREATE TABLE IF NOT EXISTS orders (
    order_id      INTEGER PRIMARY KEY,
    owner         TEXT NOT NULL,
    type_id       INTEGER NOT NULL,
    is_buy        INTEGER NOT NULL,
    issued        TEXT NOT NULL,
    issued_by     INTEGER,
    price         REAL,
    volume_total  INTEGER,
    volume_remain INTEGER,
    state         TEXT,
    location_id   INTEGER,
    raw           TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

-- CCP's reference prices, one row per type per UTC day, only for types the ledger has met: the
-- fallback opening price, and a dated record of what the market thought of each type.
CREATE TABLE IF NOT EXISTS prices (
    type_id        INTEGER NOT NULL,
    day            TEXT NOT NULL,
    average_price  REAL,
    adjusted_price REAL,
    PRIMARY KEY (type_id, day)
);

-- What stock the ledger never saw arrive is worth, per type: fixed once, at the cutover. `source` says
-- which figure it is ('forge_history' - the median daily average in The Forge over the 30 days up to
-- the cutover - or 'ccp_average' when that region never traded the type), `basis` the day it describes.
CREATE TABLE IF NOT EXISTS opening_prices (
    type_id INTEGER PRIMARY KEY,
    price   REAL NOT NULL,
    source  TEXT NOT NULL,
    basis   TEXT NOT NULL,
    fetched TEXT NOT NULL
);

-- One row per document read by a sync, so `doctor` and `ledger sync` can say how far back each
-- source reaches and when it was last refreshed.
CREATE TABLE IF NOT EXISTS sync_log (
    source     TEXT NOT NULL,
    synced_at  TEXT NOT NULL,
    rows_seen  INTEGER NOT NULL,
    rows_new   INTEGER NOT NULL,
    oldest     TEXT,
    newest     TEXT,
    PRIMARY KEY (source, synced_at)
);

CREATE INDEX IF NOT EXISTS transactions_date ON transactions (date);

CREATE INDEX IF NOT EXISTS journal_date ON journal (date);

CREATE INDEX IF NOT EXISTS journal_context ON journal (context_id_type, context_id);

CREATE TABLE IF NOT EXISTS notes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL,
    body      TEXT NOT NULL DEFAULT '',
    status    TEXT,
    closed_at TEXT,
    source    TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    synced_at TEXT PRIMARY KEY,
    data      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS notes_ts ON notes (ts);

CREATE TABLE IF NOT EXISTS stock_snapshots (
    ts          TEXT NOT NULL,
    type_id     INTEGER NOT NULL,
    class       TEXT NOT NULL,
    held        INTEGER NOT NULL,
    listed      INTEGER NOT NULL,
    unit_cost   REAL,
    unit_market REAL,
    PRIMARY KEY (ts, type_id)
);

CREATE INDEX IF NOT EXISTS stock_snapshots_type ON stock_snapshots (type_id, ts);
"""

LEDGER_TABLES = ("transactions", "journal", "orders", "prices", "opening_prices", "sync_log", "notes",
                 "snapshots", "stock_snapshots")
SCHEMA = Schema("ledger", (LEDGER_V1,), LEDGER_TABLES)


# -- argument setup, as moved from the legacy plugin ----------------------------------------------

def _ledger(p_ledger):
    ledger_sub = p_ledger.add_subparsers(dest="ledger_action")
    p_lsync = ledger_sub.add_parser(
        "sync", help="copy corporation and member jobs, wallets, orders and blueprints from ESI into the "
                     "ledger; run at least every few weeks - ESI keeps only 30 days of wallet history")
    p_lsync.add_argument("--char", help="only the corporation of this stored character, read by it first")
    p_lsync.add_argument("--no-personal", action="store_true",
                         help="skip the members' personal wallets, jobs, orders and blueprints")
    p_lsync.add_argument("--json", action="store_true", help="machine-readable output")
    p_lpnl = ledger_sub.add_parser("pnl", help="profit and loss: revenue, fees, cost of goods sold, overhead")
    p_lpnl.add_argument("--since", metavar="YYYY-MM-DD", help="first day included (default: the cutover)")
    p_lpnl.add_argument("--until", metavar="YYYY-MM-DD", help="first day excluded (default: no end)")
    p_lpnl.add_argument("--by", choices=["total", "day", "week", "month"], default="total",
                        help="one statement for the whole period (default), or a row per day/week/month")
    p_lpnl.add_argument("--json", action="store_true", help="machine-readable output")
    p_lprod = ledger_sub.add_parser("products", help="per product: units built and their unit cost, units "
                                                     "sold, net price and profit")
    p_lprod.add_argument("--since", metavar="YYYY-MM-DD", help="first day included")
    p_lprod.add_argument("--until", metavar="YYYY-MM-DD", help="first day excluded")
    p_lprod.add_argument("--scope", choices=["all", "invention", "other", "disposal", "planetary"], default="all",
                         help="invention lines (built from blueprints the business invented), other jobs, "
                              "disposal (pre-existing stock sold as-is, no job of ours ever made it), planetary "
                              "(colony output), or all")
    p_lprod.add_argument("--limit", type=int, metavar="N", help="only the N most profitable rows")
    p_lprod.add_argument("--json", action="store_true", help="machine-readable output")
    p_lmkt = ledger_sub.add_parser("markets", help="per station sold at: units, revenue, tax and fees, profit")
    p_lmkt.add_argument("--since", metavar="YYYY-MM-DD", help="first day included (default: the cutover)")
    p_lmkt.add_argument("--until", metavar="YYYY-MM-DD", help="first day excluded (default: no end)")
    p_lmkt.add_argument("--json", action="store_true", help="machine-readable output")
    p_lpi = ledger_sub.add_parser("planetary", help="the colonies as an operation: output, sales, customs and "
                                                    "construction, per product and per planet, and what is held")
    p_lpi.add_argument("--since", metavar="YYYY-MM-DD", help="first day included (default: everything stored)")
    p_lpi.add_argument("--until", metavar="YYYY-MM-DD", help="first day excluded (default: no end)")
    p_lpi.add_argument("--by", choices=["total", "day", "week", "month"], default="total",
                       help="add a row per day/week/month to the totals")
    p_lpi.add_argument("--limit", type=int, default=15, metavar="N", help="held commodities shown (default 15)")
    p_lpi.add_argument("--all", action="store_true", help="every held commodity")
    p_lpi.add_argument("--json", action="store_true", help="machine-readable output")
    p_linv = ledger_sub.add_parser("invention", help="per invented blueprint: decryptor, attempts, success "
                                                     "rate and cost per invented run")
    p_linv.add_argument("--json", action="store_true", help="machine-readable output")
    p_lstock = ledger_sub.add_parser("inventory", help="materials, finished goods and jobs in progress at "
                                                       "ledger cost and at live Jita prices")
    p_lstock.add_argument("--no-personal", action="store_true",
                          help="count corporation assets only, not the members' own hangars")
    p_lstock.add_argument("--limit", type=int, default=15, metavar="N",
                          help="rows per section in the table (default 15)")
    p_lstock.add_argument("--all", action="store_true", help="every row")
    p_lstock.add_argument("--json", action="store_true", help="machine-readable output")
    p_lnote = ledger_sub.add_parser("note", help="the operation's log: dated updates, decisions, incidents, "
                                                 "analyses and to-dos, kept in the ledger")
    note_sub = p_lnote.add_subparsers(dest="note_action")
    p_nadd = note_sub.add_parser("add", help="store a note")
    p_nadd.add_argument("title", help="one line: what happened, what was decided, what is to do")
    p_nadd.add_argument("--kind", choices=list(ledger_db.NOTE_KINDS), default="update",
                        help="default update; a todo stays open until `ledger note done ID`")
    p_nadd.add_argument("--body", help="the note's text (Markdown is fine)")
    p_nadd.add_argument("--body-file", metavar="PATH", help="read the text from a file, or - for stdin")
    p_nadd.add_argument("--at", metavar="ISO", help="when it happened, e.g. 2026-09-24T05:47:00Z (default now)")
    p_nadd.add_argument("--source", help="where the note came from, e.g. a file it was imported from")
    p_nadd.add_argument("--json", action="store_true", help="machine-readable output")
    p_nlist = note_sub.add_parser("list", help="notes newest first, one line each")
    p_nlist.add_argument("--kind", choices=list(ledger_db.NOTE_KINDS), help="only this kind")
    p_nlist.add_argument("--since", metavar="YYYY-MM-DD", help="only notes from this day on")
    p_nlist.add_argument("--open", action="store_true", help="only open to-dos")
    p_nlist.add_argument("--grep", metavar="TEXT", help="only notes whose title or text contains TEXT")
    p_nlist.add_argument("--limit", type=int, metavar="N", help="only the newest N")
    p_nlist.add_argument("--json", action="store_true", help="machine-readable output")
    p_nshow = note_sub.add_parser("show", help="notes in full: by id, or the newest (default 1)")
    p_nshow.add_argument("ids", nargs="*", type=int, help="note ids")
    p_nshow.add_argument("--last", type=int, default=1, metavar="N", help="without ids: the newest N")
    p_nshow.add_argument("--kind", choices=list(ledger_db.NOTE_KINDS), help="without ids: only this kind")
    p_nshow.add_argument("--json", action="store_true", help="machine-readable output")
    p_ndone = note_sub.add_parser("done", help="close a to-do")
    p_ndone.add_argument("id", type=int)
    p_ndone.add_argument("--json", action="store_true", help="machine-readable output")
    p_lprog = ledger_sub.add_parser("progress", help="the figures every sync recorded (jobs, T2 built and "
                                                     "sold, profit, stock, WIP), open to-dos and recent notes")
    p_lprog.add_argument("--since", metavar="YYYY-MM-DD", help="only snapshots from this day on")
    p_lprog.add_argument("--all", action="store_true", help="every sync, not one row per day")
    p_lprog.add_argument("--notes", type=int, default=5, metavar="N", help="recent notes shown (default 5)")
    p_lprog.add_argument("--json", action="store_true", help="machine-readable output")
    p_lsql = ledger_sub.add_parser("sql", help="one read-only SELECT or WITH query against the ledger file, with "
                                               "type_name(id), character_name(id) and local_time(ts[, zone])")
    p_lsql.add_argument("query", help="one SELECT or WITH statement (quote it); anything else is refused")
    p_lsql.add_argument("--limit", type=int, default=200, metavar="N",
                        help="at most N rows (default 200); the output says when it cut")
    p_lsql_out = p_lsql.add_mutually_exclusive_group()
    p_lsql_out.add_argument("--json", action="store_true", help="machine-readable output")
    p_lsql_out.add_argument("--csv", action="store_true", help="CSV rows on stdout (the cut note goes to stderr)")
    p_lschema = ledger_sub.add_parser("schema", help="the ledger's tables with their columns and types, read "
                                                     "from the file")
    p_lschema.add_argument("table", nargs="?", help="only this table")
    p_lschema.add_argument("--json", action="store_true", help="machine-readable output")


COMMANDS = (
    Command(("ledger",), help=("accounting ledger: book industry jobs, purchases and sales into a local SQLite "
                               "file and report cost, profit and stock value (needs login --scopes jobs,wallet,"
                               "orders,corp-orders,blueprints)"),
            configure=_ledger, handler=cmd_ledger.cmd_ledger),
)


def plugin() -> Plugin:
    return Plugin(
        name="ledger",
        version=__version__,
        api=API_VERSION,
        summary="the accounting ledger: costs, profit, stock value, notes and progress",
        requires=("industry",),
        enhances=("planetary",),
        commands=COMMANDS,
        tools=tools.TOOLS,
        panels=panels.PANELS,
        hooks={"sync_if_due": ledger_sync.sync_if_due},
        watch=(WatchSource("ledger", poll=watch.ledger_poll, wanted=watch.ledger_wanted),),
        scopes={feature: list(sso.OPTIONAL_SCOPES[feature]) for feature in FEATURES},
        tables=(SCHEMA,),
    )
