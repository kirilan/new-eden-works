# The accounting ledger

`ledger` books industry jobs, wallet journal and transactions, orders and blueprints into a local SQLite
file, and reports what the business cost, earned and holds.

```bash
neworks login --scopes jobs,wallet,orders,corp-orders,blueprints,assets   # once per member
neworks ledger sync                        # copy jobs, wallets, orders, blueprints into the ledger
neworks ledger pnl                         # profit and loss since the cutover
neworks ledger pnl --since 2026-09-19 --by week
neworks ledger products --scope invention  # unit cost, net price and profit per T2 line
neworks ledger markets                     # revenue, tax and fees, profit per station sold at
neworks ledger invention                   # decryptor, success rate, cost per invented run
neworks ledger planetary --by week         # the colonies: output, sales, customs, per planet and product
neworks ledger inventory                   # stock and work in progress at cost and at Jita
neworks skills --watch --ledger            # keep syncing, at most once an hour
neworks ledger progress                    # the figures every sync recorded, open to-dos, recent notes
neworks ledger note add "Sell run loaded" --body-file notes.md   # the operation's log
neworks ledger note add "Relist Gyro II in Dodixie" --kind todo
neworks ledger note list --open            # open to-dos; `note show [ID...]`, `note done ID`
neworks ledger schema jobs                 # tables, columns and types, read from the file
neworks ledger sql "select character_name(installer_id), type_name(product_type_id), runs,
    local_time(end_date) from jobs where status = 'active' order by end_date"
```

ESI forgets: wallet journal and transactions are served for **30 days**, completed industry jobs for
about 90, and an invented copy disappears from `/blueprints` when its last run is used. `ledger sync`
copies all of it into one SQLite file (`ledger.sqlite3` in the data root) before that happens, keyed by
ESI's own ids, so it can run any number of times — by hand and from `watch` at once — and never
double-counts. It reads every player corporation a stored character belongs to (jobs, all seven wallet
divisions, open and historical orders, blueprints) with whichever consenting member ESI accepts, and
each member's personal side too, so a sale that went through a personal wallet is still booked. A
personal row that ESI flags `is_personal: false` was made for the corporation and is already in its
wallet, so it is dropped. Corporation wallets need the in-game **Accountant** or **Junior Accountant**
role. `doctor` warns when the last sync is 20 days old and fails past 30.

Only facts are stored; every report replays them in time order, so a better costing rule re-prices all
of history. The rules:

- **Weighted average cost** per item type and per blueprint's copy runs. Purchases, and broker fees on
  buy orders, add to a type's cost; a finished job adds its whole cost as the value of its output.
- **Stock older than the ledger is valued at market**: when a job or sale needs more than the ledger
  saw arrive, the shortfall is priced at the type's median daily average in The Forge over the 30 days
  up to the *cutover* (the first day with a stored trade or job), fixed once per type. A type with no
  price is counted and reported, never costed as free.
- **Job inputs come from the SDE**, because ESI never lists them: manufacturing materials at the
  blueprint's ME (from the copy a sync saw, its siblings, or the ME invention gives), invention data
  cores plus one decryptor per attempt. Fees come from the journal by job id.
- **Invention cost lands on the successes**: an invention job's whole cost, failed attempts included,
  becomes the value of the copy runs it made, and so of every T2 unit built from them.
- **The decryptor is read off the copies** (ME/TE of an invented copy), or, if no sync saw a copy, from
  the runs manufacturing drew from one copy. Parity and Optimized Attainment leave the same ME/TE; the
  one the business bought wins.
- **Broker fees follow their order**: matched by the second an order was issued, or — because ESI moves
  `issued` to an order's last modification — to the next order its issuer modified whose value fits
  that issuer's measured fee rate. Sales tax is matched to the sales of the same second.
- Trades between the business's own wallets are skipped on both sides.

Reports split **invention lines** (products built from blueprints the business invented) from **other**
(products some manufacturing or reaction job of ours made, from a bought or recovered blueprint), from
**disposal** (stock sold that no job of ours ever made - recovered loot, surplus minerals, old ships -
shown apart and left out of total revenue, gross and net profit, because cashing out what was already
there is not something the operation earned; `ledger products --scope disposal` lists it) and from
**overhead** (office rent, contract fees, research jobs, cancelled jobs, fees that matched nothing, and
GM cash transfers such as a reimbursed fee). Each ends with an *assumptions* block counting every place
a rule had to assume something — copies older than the ledger costed at 0, unknown ME or decryptor,
fees taken from the job row — so a figure is never more certain than it looks. `inventory` is the one
report that reads ESI live: today's corporation and member assets plus open sell orders, valued at
Jita's best sell (CCP's average where Jita has none, marked `~`), beside their ledger cost (`*` marks
opening-price cost), and running jobs at cost so far and expected output value. `markets` splits the
same invention-plus-other figures by the station each sale happened at (its sales tax with it, and a sell
order's broker fee where the order was placed), so its rows add up to the period's gross profit. Every
report takes `--json`.

**Planetary industry** is a separate operation with its own scope, `planetary`, reported beside the total
and never inside it (`ledger pnl` prints it under the statement, with *net profit including planetary*;
`--by` rows carry a *planetary profit* column). A colony's output never passes a wallet or a job, so the
ledger measures it from our holdings: when any member has a colony, each full `ledger sync` (no `--char`,
no `--no-personal`) also polls every member's and the corporation's assets — hangars, ships and customs
offices — plus our open sell orders for every planetary commodity, and stores the totals
(`pi_polls`/`pi_holdings`, schema v5; needs `--scopes planets,assets`). The rules:

- **Output is what holdings gained.** Between two polls a type's output is its rise in holdings plus what
  we sold minus what we bought. A fall (goods destroyed, given away, imported to a planet) is never
  negative output, and output only counts again once the running figure is back above its previous high,
  so nothing is counted twice. The first poll is the baseline: what was held then is pre-existing stock.
- **A poll is dated by its oldest document's `Last-Modified`**, not by the sync: ESI serves assets up to an
  hour old, and a sale that happened after that moment is compared to the next poll instead.
- **A poll is all or nothing.** A document nobody may read (no consent, no Director role for the corporation
  hangars) is left out of every poll alike; one that fails this time cancels the poll, because a hangar
  missing from one poll would look like stock vanishing and coming back as output. When the set of
  documents read changes, the next poll starts a new baseline.
- **Output costs nothing** - extraction consumes no stock - and enters the pool at the first sale in its
  interval (so that sale draws on it) or at the poll. **Customs** (export and import tax), **colony
  construction** (command-centre upgrades and structures) and **command centres** bought on the market are
  the colonies' costs, booked when paid and not spread over the output.
- A planetary commodity is `planetary` once our colonies have made some of it, for sales from the first poll
  on; sold earlier, or never made by us (recovered P1, say), it stays disposal.

`ledger planetary` reports the scope: the statement for the period (`--since`, `--until`, `--by` adds a row
per period), output and sales per product, each planet's charges (export tax, import tax, construction,
which character paid; ESI ties a charge to a planet, and output to none), and what the newest poll found
held, valued at CCP's average. Goods imported to a colony are not carried into what it makes, so a factory
planet's output is costed at 0; the assumptions block says so whenever an import tax was paid.

The same file is the operation's **log**. `ledger note` keeps dated notes — `update` (the default),
`decision`, `incident`, `analysis` and `todo`, which stays open until `note done` — with a Markdown body
from `--body`, a file or stdin, and `--at` to backdate one. Every sync also stores a **snapshot** of the
figures (jobs running and ready per activity, T2 units built and sold, invention revenue and gross
profit, net profit, stock at cost, work in progress, open sell and buy orders), so `ledger progress`
shows the trend one row per day (`--all` for every sync) beside the open to-dos and the latest notes.

For a question no report answers, `ledger sql "SELECT ..."` queries the file directly; no `sqlite3`
binary is needed. It opens the file read-only (`file:...?mode=ro` and `PRAGMA query_only`), and refuses
anything but one `SELECT` or `WITH` statement, so a query can never change the books. At most
`--limit` rows (default 200) are printed, and the output says when it cut; `--json` gives `columns`, `rows`
(one object per row), `count` and `cut`, and `--csv` plain rows. Three functions save rebuilding the usual
look-ups, all from local files (no ESI request per row):

| Function | Returns |
|---|---|
| `type_name(type_id)` | The name from the local type and name caches that other commands fill; unknown -> `type <id>`. |
| `character_name(id)` | A stored character's name; anyone else -> the id as text. |
| `local_time(ts[, zone])` | An ISO UTC stamp as `YYYY-MM-DD HH:MM` in the zone (an IANA name or an offset; default: the operation file's `planning.timezone`, else UTC). |

`ledger schema [TABLE]` lists the tables with their columns and types, read from the file itself.
