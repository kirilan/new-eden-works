# Planetary industry

Planetary industry from two sides: `pi` works out recipe trees, colony fits and planet yields from the
local game data, and `colonies` reads your live colonies from ESI.

## `pi` — planetary industry: recipe trees, colony fits, planet yields

```bash
neworks pi chain "Broadcast Node"                   # every step down to the raw materials, priced
neworks pi chain 2867 --hub amarr                   # a type id, shopped at Amarr's station
neworks pi chain "Data Chips" --region Heimatar     # price off a whole region's book instead
neworks pi chain "Broadcast Node" --customs-rate 5  # add what a 5% customs office takes per step
neworks pi fit --ccu 4 --launchpad 1 --ecu 2 --basic 2 --advanced 1 --link-allowance 500,400
neworks pi fit --ccu 3 --ecu 1 --heads 12           # a layout that cannot work, and which rule stops it
neworks pi planet-type Barren                       # one planet's raws and all it can refine alone
```

Nothing here logs in. Recipes, structure costs, command-centre budgets and the per-unit customs values come
from the local SDE snapshot — build 3494416 ships in the package and `update-data` refreshes it: 68
schematics (15 tier 1, 24 tier 2, 21 tier 3, 8 tier 4), 83 commodities on five customs tiers, eight planet
types, and the CPU/powergrid every structure draws — and only `pi chain` touches the network at all, for
prices, on the public order-book endpoints. `fit` and `planet-type` answer from the snapshot alone; their
tests assert that not one request leaves the process.

```text
$ neworks pi chain "Broadcast Node"
Broadcast Node (id 2867) - planetary recipe tree per 1 unit of product, SDE build 3494416
commodity                  tier  qty/unit  facility   cycle   planets                     price/u       va/fac-hr
-------------------------  ----  --------  ---------  ------  --------------------------  ------------  ----------
Broadcast Node             4     1         high-tech  1h 00m                              1,980,000.00  321,240.00
  Data Chips               3     6         advanced   1h 00m                              104,600.00    -4,100.00
    Microfiber Shielding   2     20        advanced   1h 00m                              17,400.00     29,192.00
      Industrial Fibers    1     160       basic      30m                                 720.60        4,704.00
        Autotrophs         0     24,000    -          -       Temperate                   4.02          -
      Silicon              1     160       basic      30m                                 724.60        -9,776.00
        Felsic Magma       0     24,000    -          -       Lava                        6.46          -
...
  prices: cheapest standing ask at Jita 4-4 (station), as of 23:38:25Z (4m 23s ago; ESI refreshes the book
  every 5 min); 28 order-book requests now
```

`qty/unit` is what **one unit of the product** needs, not what one facility cycle makes: each quantity is
scaled through its own schematic's per-cycle ratio, so tier 1 sits under this tree at 24,000 units of raw
material for a single Broadcast Node. That is also why the output is an indented tree instead of a summed
shopping list — Silicon appears twice, once under Data Chips and once under Silicate Glass, each at the
quantity that branch needs, because the two branches can be run on different colonies.

`va/fac-hr` answers the question a colony actually turns on: what one hour of that facility is worth. It is
`(output × price − inputs × prices) ÷ cycle hours` from the schematic's own per-cycle quantities, so it does
not change with scale and is the figure for the tree above as much as for one cycle. Negative rows are kept
rather than hidden — Data Chips loses 4,100 an hour at today's asks, which is the fact that makes someone
sell Microfiber Shielding instead. A step whose output or any input has neither an ask in scope nor a
published price prints `-` for `va/fac-hr` and names the type in a note: an unpriced participant is never
counted as free.

Prices are why `chain` asks for anything. The default scope is Jita 4-4; `--hub` picks another trade hub and
`--region` prices off a whole region's book, and the two together are refused because they disagree about
what "the market" is. There is no install fee in planetary industry, so there is no `--system`: unlike
`build-cost`, nothing here needs a system to bill, only a scope to read asks from. One order book per
distinct type in the tree — 28 for Broadcast Node — and ESI's megabyte `/markets/prices` document is fetched
only when some participant actually lacks an ask; a second run inside the quote-cache window says so
instead: `no order-book request: all 28 types came from the local quote cache, which ESI's own expiry says
is still current`.

Customs is the one number this command cannot look up. ESI publishes a customs office's rate to the
corporation that owns it and to nobody else, so `--customs-rate PCT` is yours to supply — validated 0..100
before any book is opened. Each step is then billed from the SDE's own per-unit values:
`PCT × (inputs×tax × 0.5 + output×tax × 1.0)`, the import and export factors read from the document rather
than hard-coded, so a CCP change arrives with `update-data`. The root row above is checkable by hand: 18
units of tier-3 input at 60,000 each discounted to half (540,000) plus one tier-4 output at 1,200,000, times
5% = 87,000. Customs gets its own column and is never folded into `va/fac-hr`, because a corporation sets
the rate to change behaviour and the point of the table is to see *which* step the rate punishes.

```text
$ neworks pi fit --ccu 4 --launchpad 1 --ecu 2 --basic 2 --advanced 1 --link-allowance 500,400
Planetary colony fit - command centre upgrade level 4, SDE build 3494416
item                           cpu each  pg each  count  cpu     pg
-----------------------------  --------  -------  -----  ------  ------
launchpad                      3,600     700      1      3,600   700
extractor control unit         400       2,600    2      800     5,200
basic industry facility        200       800      2      400     1,600
advanced industry facility     500       700      1      500     700
planetary links                500       400      1      500     400
total                                             7      5,800   8,600
command centre level 4 budget                            21,315  17,000
free                                                     15,515  8,400

  each extractor head costs 110 CPU / 550 PG (the ECU's own head figures in the local SDE)
  the free budget would carry 141 heads on CPU and 15 on powergrid; 2 extractor control units can attach at most 20
  fits: yes - room for 15 extractor heads; powergrid binds first
```

The command-centre ladder is what `--ccu` selects — 1,675 CPU / 6,000 PG at level 0 up to 25,415 / 19,000 at
level 5 — and the measurement behind this command is that those figures, and every structure's cost, are
**the same for all eight planet types** in build 3494416. That is why `fit` takes no planet: it would be a
parameter nobody needs. The check is kept in the code rather than in a comment — if a future SDE makes one
planet's budget differ, the command refuses and names the disagreement instead of quietly averaging eight
numbers. Powergrid is what binds real colonies: on the layout above CPU would carry 141 more heads and
powergrid 15, so powergrid decides, and the note says both. Four layouts were measured against this ladder:
the one above fits 15 heads; the same shape at level 5 (free 19,615 / 10,400) fits 18; four ECUs, four basic
and two advanced facilities at level 4 leave only 900 PG and fit 1 head; the identical layout at level 5 fits
5. One launchpad alone eats 3,600 CPU — a fifth of a level-4 budget — which is why `--launchpad` is the first
number worth arguing about.

Heads are charged what the SDE says an ECU's head costs (110 CPU / 550 PG), and at most ten attach to one
ECU. That cap is a documented game rule, not a dogma value: it was looked for and not found in the data —
of 2,867 attributes on build 3503375 only `ecuExtractorHeadCPU` (1690) and `ecuExtractorHeadPower` (1691)
describe heads at all, `/universe/types/2848` publishes no head count, and `/universe/dogma/*` returns 404 —
so it is taken from EVE University's *Planetary buildings* page (read 2026-09-13: "every head (up to a
maximum of 10) needs an amount of Powergrid (550) and CPU") and carried as a named constant. Ask for twelve
heads on one ECU and the answer is a verdict, not a stack trace: `fits: no - more heads than the units can
carry: 12 heads against 1 x 10 = 10`, exit 0, because the question that makes someone run this is a near
miss. Over budget is reported the same way — level 0 with a launchpad and an ECU prints the table, then
`fits: no - CPU over budget by 2,325.00`. Only nonsense is refused: a negative count, `--ccu` outside 0..5,
a malformed `--link-allowance`.

`--link-allowance CPU,PG` is your figure, and the output labels it as one. What keeping inter-planetary
links up costs depends on which links a colony has, and ESI publishes nothing about it; charging an
invented default would put a number you did not choose into a budget decision.

```text
$ neworks pi planet-type Barren
Barren (planet type 2016) - what it yields and refines with no imports, SDE build 3494416
raw material      refines into (tier 1)
----------------  ---------------------
Aqueous Liquids   Water
Base Metals       Reactive Metals
Carbon Compounds  Biofuels
Microorganisms    Bacteria
Noble Metals      Precious Metals

product                        tier  facility  cycle
-----------------------------  ----  --------  ------
Biocells                       2     advanced  1h 00m
Mechanical Parts               2     advanced  1h 00m
Nanites                        2     advanced  1h 00m
Test Cultures                  2     advanced  1h 00m
Water-Cooled CPU               2     advanced  1h 00m
Transcranial Microcontrollers  3     advanced  1h 00m
  no tier-4 product is reachable on Barren with no imports; no planet type in SDE build 3494416 reaches one
  alone, because every tier-4 recipe wants at least one input another planet's extractors yield
```

`planet-type` closes the recipe set over the schematics that run on that planet alone — a product counts
only if everything feeding it is itself reachable without an import. The closure was cross-checked against
an independent from-scratch walk over all eight planet types with zero disagreements, and the result worth
knowing before planning a nine-character operation is in the note above: **no single planet type reaches
tier 4 on its own**, because every tier-4 recipe wants at least one input another planet's extractors yield.
A shorter list than expected is a fact about the SDE, so it is said out loud instead of leaving an empty
table to be misread as a bug.

Machine output follows the same rules as the rest of the tool. `chain --json` reports the product, the
pricing scope and every step with `qty_per_unit_of_product`, `price_per_unit`, `price_basis`
(`min_sell` or `esi_adjusted`), `value_added_per_facility_hour`, `customs_per_facility_hour` and the lists of
`unpriced_participants` / `untaxed_participants` that made any of those a null; `--csv` writes one row per
step to stdout with the notes on stderr. `fit --json` keeps the verdict machine-readable: `heads.binding` is
a key from `heads.limits` (`cpu`, `powergrid`, `per_ecu_cap`, ties joined with `+`) so a spreadsheet can
pivot on it, while the text says "CPU and powergrid" — and `over_budget`, `fits` and `reasons` separate what
the layout costs from whether it is allowed. `planet-type --csv` writes one row per commodity from tier 0
down, with each product's inputs in its own cell.

## `colonies` — what is actually on your planets right now

```bash
neworks login --scopes planets          # once, per character, in the browser
neworks colonies                        # every colony of every stored character
neworks colonies --char AdaVane --detail
neworks colonies --csv > colonies.csv
```

`pi` answers what a colony *could* make from the local SDE; `colonies` answers what you have and what it is
doing. One row per colony: system, planet id, planet type, command-centre level (`upgrade_level`), ESI's own
pin count, and how long ago that view was taken. `--detail` fetches each colony's pins and adds two tables —
every extractor with its product, quantity per cycle, cycle time, head count and the instant its programmed
run ends (`expired 2h 00m ago`, `in 2d 23h`), sorted so the ones that ran out come first, and every facility
running a schematic with what it makes. Pins that are neither (ECUs, command centres) are counted in one line
instead of listed.

Two properties of ESI's planetary data shape everything else here:

- **A colony is only recalculated when someone opens it.** Verbatim from ESI's own description of the endpoint
  (fetched 2026-09-13): *"Note: Planetary information is only recalculated when the colony is viewed through
  the client. Information will not update until this criteria is met."* `last_update` can lag by months, and a
  stale stamp does not mean the colony is gone — it means nobody has looked at it in game. Rows whose layout is
  older than 7 days carry a `*`, explained under the table. Seven days was set against the cycle times in the
  shipped data (1800–3600 s): a week without a client view is 168 to 336 cycles of drift, and it is also a full
  planetary-industry round for most chains.
- **Everything about an extractor comes from ESI itself.** `extractor_details` carries the product type,
  `qty_per_cycle`, `cycle_time`, the heads and the absolute `expiry_time`; the local snapshot only puts names on
  ids and says what a facility's schematic produces. So `--detail` still works with no SDE installed: it prints
  a notice that names came from `/universe/names` and that no recipe is published there, rather than refusing
  over a ~100 MB download. The same applies to a schematic the snapshot has never heard of — its name comes
  from `/universe/schematics/<id>`, which publishes no recipe, and the row says so instead of showing an empty
  `makes` cell.

Cost: the summary is one request per consenting character; `--detail` adds one per colony. Both routes publish
`x-client-cache-ttl: 600` inside the `char-industry` group and the transport will not re-request inside that
window, so nothing here polls faster than ESI says its own data is fresh — and both routes are GET only, so
nothing here can write to the game. A character whose token lacks the scope prints a hint instead of failing the
run (see [adding consent](login.md#adding-characters-extra-consent-logging-out)).

Machine output: `--json` gives one entry per character with each colony's ESI fields plus `age_seconds`, `stale`
and `layout_fetched`, and — where a layout was read — `pins_read`, `extractors` (each with `seconds_to_expiry`,
negative once the run ended) and `facilities`; consent hints, per-colony fetch warnings and naming notices
travel as the `hints`, `warnings` and `notices` arrays. `--csv` follows the flag: one row per colony by default,
one row per extractor with `--detail`, where the colony columns repeat so every row says where that extractor
stands and a colony without one keeps its row with those cells empty. A colony whose pin document ESI refused
keeps its summary row, loses only its own pins, and says why on stderr; the run still exits 0.
