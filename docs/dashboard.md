# Dashboard

`dashboard` serves a local web page with profit over time, by product and by market, planetary industry,
the stockpile and industry slots.

```bash
neworks dashboard serve                         # http://<this machine>:8765/, rebuilt every 60 minutes
neworks dashboard serve --host 127.0.0.1 --port 9000 --since 2026-09-19
neworks dashboard build --out dashboard.json    # the same data once, as JSON
```

`serve` runs a small web server (standard library only) and one refresh thread. Every `--refresh` minutes
(default 60) it runs `ledger sync` when the last sync is that old (`--no-sync` leaves syncing to someone
else), replays the ledger, and runs the panels' live reports: `industry status --corp` for slots and jobs,
`colonies --detail` for extractor timers (`--no-live` skips them). A plugin's quick panel, if one is
loaded, refreshes more often, every `--orders-refresh` minutes (default 20; 0 turns it off). The page polls for new data every 5
minutes and has a *Refresh now* button, accepted at most once per 5 minutes.

The page shows net profit and its parts for the last 7 days, 30 days or since `--since` (default the
cutover); gross profit per day or ISO week and the running net profit; profit per product (invention lines
and other builds) and per market station, each with a table view; and industry slots per character with
jobs ready to deliver and running jobs by end time. The figures are `ledger pnl`'s: disposal is left out
and the assumptions block is printed underneath.

The **planetary industry** panel is `ledger planetary` for the same period: the colonies' own profit (never
part of the net profit above) with revenue, customs, construction, tax and fees, units of output and the
value of what the newest poll found held; planetary profit per day or week; output and sales per product;
and every colony from `colonies --detail` with the soonest extraction to end first (*ended*, *ends soon*
inside 12 hours, *running*), what leaves the planet, the extractors' heads and when ESI last saw it opened.
Under it, each planet's charges and the planetary commodities held.

The **stockpile** part answers two questions about the *current* lines: products with a manufacturing or
invention job that started or was still running in the last 7 days. Stock of any other product is listed
apart under *Old lines* (to sell down, not a line), and an input only old lines used is left out. *Is a
line piling up?* Per finished good: units held and listed
(live, from `ledger inventory`), units built and sold per day over the last 14 days (or since the line's
first job, if newer), and days of stock (on hand / sold per day): over 14 days or nothing sold is *piling
up*, 7-14 is *slow*. *Are we running out of an input?* Per item jobs consume (materials, data cores,
decryptors, T1 modules): units held, units the current lines' jobs used per day (from the ledger's recipes),
and days of cover: none left is *out*, under 3 days *low*, under 7 *watch*, and *short* when an idle copy of
a current line cannot start one job at the factory for want of it (from `industry status`). History comes from
two places: the ledger reconstructs finished goods on hand day by day from jobs and sales, back to the
cutover; and every refresh stores the measured stock per type in the ledger (`stock_snapshots`, schema v4),
so hangars and materials have a measured history from the first refresh on.

**Panels and pages.** Each part of the page is a **panel** offered by a plugin: `ledger.profit` (net
profit, the charts, products and markets), `ledger.planetary` (the planetary industry panel, and the
charges and holdings under the colonies), `planetary.colonies`, `ledger.stockpile` and `industry.slots`,
plus whatever other plugins offer. A panel from a plugin that is not loaded is not on the page, and
`plugins` lists each plugin's panels. Each panel is read on its own: one
that fails keeps its last good data with a note saying why, and the other panels still refresh. The
default is every panel on one page in the order above; config.json's `dashboard.pages` chooses the
pages, and which panels appear on each and in what order (a page tab appears when there are several):

```json
"dashboard": {"pages": [
  {"title": "Operation", "panels": ["ledger.profit", "ledger.planetary", "planetary.colonies", "ledger.stockpile"]},
  {"title": "Industry", "panels": ["industry.slots"]}
]}
```

A panel named there that no loaded plugin offers is skipped with a note at the top of the page. A plugin's
own panel can be of kind `kpi`, `table`, `list` or `timeseries`, which the page draws from its data alone.

It listens on every interface (`--host 0.0.0.0`) so other machines on the local network can open it. There
is no login: anyone who can reach the port sees the figures and character names, and can ask for an early
refresh. Keep it behind your router or bind it to `127.0.0.1`. The last document is cached in the cache
root (`dashboard.json`), so a restarted server shows data at once.

**Running it continuously.** The service's `dashboard` job runs `dashboard serve` and restarts it 30 s
after a crash; turn it on in config.json as [service](automation.md#service-and-run--every-scheduled-job-in-one-process)
shows. A refresh that fails (ESI down, a report erroring) is logged and the page keeps the last good data,
showing the error. A service running from a source checkout runs whichever branch is checked out there and
picks up new code (and a new page) only when restarted. The ledger refuses to be opened by code older than
its schema ("written by a newer version"), so switching such a checkout to an older branch stops the jobs
until it is switched back.
