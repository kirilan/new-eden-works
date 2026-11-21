"""The ledger schema as eve-skills wrote it, version by version, before per-plugin migrations.

Frozen copies, used only to build old ledger files for upgrade tests. Versions 1 to 5 were
`ledger_db._SCHEMA_V1` .. `_SCHEMA_V5`, version 6 added `production_store.SCHEMA` (here `_SCHEMA_V6`).
The live code now creates the same tables from each owner's version 1 (`ledger_db.*_V1`), and
`tests.test_store` checks that both produce identical schemas. Never edit these.
"""

_SCHEMA_V1 = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per industry job, whoever owns it. `owner` is 'corp:<id>' or 'char:<id>'. Status,
-- successful_runs and completed_date change while ESI still serves the job, so they are refreshed.
CREATE TABLE jobs (
    job_id            INTEGER PRIMARY KEY,
    owner             TEXT NOT NULL,
    activity_id       INTEGER NOT NULL,
    status            TEXT,
    installer_id      INTEGER,
    facility_id       INTEGER,
    blueprint_id      INTEGER,
    blueprint_type_id INTEGER,
    product_type_id   INTEGER,
    runs              INTEGER NOT NULL,
    licensed_runs     INTEGER,
    successful_runs   INTEGER,
    probability       REAL,
    cost              REAL,
    start_date        TEXT NOT NULL,
    end_date          TEXT,
    completed_date    TEXT,
    raw               TEXT NOT NULL,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL
);

-- Market transactions. `wallet` is 'corp:<id>:<division>' or 'char:<id>'; the same trade between two
-- of our own wallets is two rows, which is exactly how the engine recognises an internal transfer.
CREATE TABLE transactions (
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
CREATE TABLE journal (
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
CREATE TABLE orders (
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

-- Every blueprint seen by a sync. An invented copy's ME/TE is how the ledger learns which decryptor
-- made it; ESI's job rows never say.
CREATE TABLE blueprints (
    item_id    INTEGER PRIMARY KEY,
    owner      TEXT NOT NULL,
    type_id    INTEGER NOT NULL,
    quantity   INTEGER,
    me         INTEGER,
    te         INTEGER,
    runs       INTEGER,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

-- CCP's reference prices, one row per type per UTC day, only for types the ledger has met: the
-- fallback opening price, and a dated record of what the market thought of each type.
CREATE TABLE prices (
    type_id        INTEGER NOT NULL,
    day            TEXT NOT NULL,
    average_price  REAL,
    adjusted_price REAL,
    PRIMARY KEY (type_id, day)
);

-- What stock the ledger never saw arrive is worth, per type: fixed once, at the cutover. `source` says
-- which figure it is ('forge_history' - the median daily average in The Forge over the 30 days up to
-- the cutover - or 'ccp_average' when that region never traded the type), `basis` the day it describes.
CREATE TABLE opening_prices (
    type_id INTEGER PRIMARY KEY,
    price   REAL NOT NULL,
    source  TEXT NOT NULL,
    basis   TEXT NOT NULL,
    fetched TEXT NOT NULL
);

-- One row per document read by a sync, so `doctor` and `ledger sync` can say how far back each
-- source reaches and when it was last refreshed.
CREATE TABLE sync_log (
    source     TEXT NOT NULL,
    synced_at  TEXT NOT NULL,
    rows_seen  INTEGER NOT NULL,
    rows_new   INTEGER NOT NULL,
    oldest     TEXT,
    newest     TEXT,
    PRIMARY KEY (source, synced_at)
);

CREATE INDEX transactions_date ON transactions (date);
CREATE INDEX journal_date ON journal (date);
CREATE INDEX journal_context ON journal (context_id_type, context_id);
CREATE INDEX blueprints_type ON blueprints (type_id);
"""


# Version 2: the operation's own log. Notes are what a person or an agent wrote down - dated updates,
# decisions, incidents, analyses and to-dos - and snapshots are the figures every sync records, so
# progress can be read back as a series instead of reconstructed from prose.
_SCHEMA_V2 = """
CREATE TABLE notes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL,
    body      TEXT NOT NULL DEFAULT '',
    status    TEXT,
    closed_at TEXT,
    source    TEXT
);

CREATE TABLE snapshots (
    synced_at TEXT PRIMARY KEY,
    data      TEXT NOT NULL
);

CREATE INDEX notes_ts ON notes (ts);
"""

# Version 3: price tracking. ESI's daily regional history is served for about 13 months and then
# forgotten like the wallet is, and a station's order book is only ever "now", so both are kept here:
# `market_history` one row per region, type and day (re-read rows replace the stored ones, since ESI
# fills the newest day in late), `book_snapshots` one reduced station book per poll, stamped with the
# book's own `Last-Modified` so two polls inside one five-minute ESI window store one row. The
# watchlist holds only what a person changed - added or excluded types; the automatic part is
# derived from sales and jobs on every read (`price_watch.watchlist`).
_SCHEMA_V3 = """
CREATE TABLE market_history (
    region_id   INTEGER NOT NULL,
    type_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,
    average     REAL,
    highest     REAL,
    lowest      REAL,
    volume      INTEGER,
    order_count INTEGER,
    PRIMARY KEY (region_id, type_id, day)
);

CREATE TABLE book_snapshots (
    location_id     INTEGER NOT NULL,
    type_id         INTEGER NOT NULL,
    ts              TEXT NOT NULL,
    polled_at       TEXT NOT NULL,
    best_sell       REAL,
    best_buy        REAL,
    sell_units      INTEGER NOT NULL,
    sell_orders     INTEGER NOT NULL,
    buy_units       INTEGER NOT NULL,
    buy_orders      INTEGER NOT NULL,
    sell_units_1pct INTEGER NOT NULL,
    sell_units_5pct INTEGER NOT NULL,
    our_sell_units  INTEGER NOT NULL,
    our_best_sell   REAL,
    PRIMARY KEY (location_id, type_id, ts)
);

CREATE TABLE watchlist (
    type_id INTEGER PRIMARY KEY,
    mode    TEXT NOT NULL CHECK (mode IN ('add', 'exclude')),
    changed TEXT NOT NULL
);

CREATE INDEX book_snapshots_type ON book_snapshots (type_id, ts);
"""

# Version 4: stock over time. Assets are only ever "now" on ESI, so the dashboard stores what is held in
# hangars and listed in open sell orders per type each time it refreshes: one row per poll and type, with
# the poll's class for it (finished good or material) and its unit cost and market price at that moment.
_SCHEMA_V4 = """
CREATE TABLE stock_snapshots (
    ts          TEXT NOT NULL,
    type_id     INTEGER NOT NULL,
    class       TEXT NOT NULL,
    held        INTEGER NOT NULL,
    listed      INTEGER NOT NULL,
    unit_cost   REAL,
    unit_market REAL,
    PRIMARY KEY (ts, type_id)
);

CREATE INDEX stock_snapshots_type ON stock_snapshots (type_id, ts);
"""

# Version 5: planetary industry. A colony's output never passes through a wallet or a job, so the only
# record of it is our holdings of planetary commodities rising: each sync that finds a colony reads every
# member's and the corporation's assets plus our open sell orders and stores one poll - only when every
# one of those documents answered, since a poll missing a character would read as that character's stock
# vanishing and coming back. `as_of` is the oldest Last-Modified among the documents read: the moment
# the poll actually describes, which is what trades are compared against (ESI caches assets for an hour).
# A type absent from a poll was held at zero.
_SCHEMA_V5 = """
CREATE TABLE pi_polls (
    ts      TEXT PRIMARY KEY,
    as_of   TEXT NOT NULL,
    sources TEXT NOT NULL
);

CREATE TABLE pi_holdings (
    ts      TEXT NOT NULL,
    type_id INTEGER NOT NULL,
    held    INTEGER NOT NULL,
    listed  INTEGER NOT NULL,
    PRIMARY KEY (ts, type_id)
);
"""


_SCHEMA_V6 = """
CREATE TABLE production_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    document TEXT NOT NULL
);
CREATE UNIQUE INDEX production_active_site ON production_rounds(site) WHERE closed_at IS NULL;
CREATE TABLE pool_handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES production_rounds(id),
    facility_id INTEGER NOT NULL,
    holder_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    hangar TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE facility_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id INTEGER NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    profile TEXT NOT NULL,
    UNIQUE(facility_id, effective_at)
);
CREATE TRIGGER facility_history_no_update BEFORE UPDATE ON facility_history
BEGIN SELECT RAISE(ABORT, 'facility history is immutable; append a dated correction'); END;
CREATE TRIGGER facility_history_no_delete BEFORE DELETE ON facility_history
BEGIN SELECT RAISE(ABORT, 'facility history is immutable'); END;
"""


BY_VERSION = (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5, _SCHEMA_V6)


def build(conn, version: int) -> None:
    """Create the tables of schema `version` the way eve-skills did, and stamp user_version."""
    for script in BY_VERSION[:version]:
        conn.executescript(script)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
