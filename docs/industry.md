# Industry

Industry jobs, blueprints, what stock allows, build costs, restock lists and the saved production
workflow. All of it reads ESI; nothing here installs a job or moves an item.

## `jobs` — industry jobs, measured run times, what was installed

```bash
neworks jobs                       # personal jobs
neworks jobs --corp --completed    # corp jobs incl. finished/cancelled
neworks jobs --corp --slots --group   # slots per character, identical jobs collapsed
neworks jobs --corp --times --finish-window 08-10,20-22 --tz Europe/Helsinki --start 09:30
                                      # measured hours per run, and run counts ending in those windows
neworks jobs --times --site home   # hours per run where an operation-file site does each activity
neworks jobs --since 08:00 --site home   # what was installed since 08:00: "did I miss anything"
```

`jobs` accepts `--char` and `--csv`; in CSV mode any consent hint is written to **stderr** so stdout
stays machine-readable. Corporation variants (`jobs --corp`, `blueprints --corp`, `inventory --corp`)
additionally need the matching director / Account-Manager role on that character; without it ESI refuses
and the tool prints a per-character warning rather than failing the whole command.

`jobs` rows are chronological by end date, and the columns mean:

| column | what it says |
| --- | --- |
| `status` | ESI's own status, plus two it does not send. **`ready`** is a job whose `end_date` has passed while ESI still calls it `active` - built and waiting for somebody to click deliver, which is the state worth acting on. **`paused`** is a job whose clock has stopped (its structure lost power, say); its `end_date` went stale when it stopped, so no time is quoted for it. |
| `activity` | manufacturing, copying, invention, reaction, or ME/TE research. An activity code CCP adds later shows as `activity N` rather than being guessed at. |
| `product` | what the job hands back. Research and copy jobs produce no new item, so that is the blueprint itself. |
| `runs` | how many times the job runs. `1 x60` is one copy licensed for sixty runs; a finished job shows `3/10` - three successful runs out of ten attempted, the figure that decides whether an invention job paid. |
| `time` | time left for a running job, otherwise when it ended. |
| `installed in` | the facility. Player structures are not nameable through `/universe/names`, so those stay a bare id. |

Both endpoints answer with running jobs only; `--completed` is what adds the delivered and
cancelled ones.

`--times` reads finished jobs too and reports, per activity, product and installer, the median hours
per run over the last `--days` days (default 14), with the range and the number of jobs. That is the
real figure after skills, implants, facility and blueprint TE, which the blueprint's base time is not.
A run is an attempt for invention and a copy for copying (so copy times also vary with the runs per
copy). Cancelled, reverted and paused jobs are left out: their end dates are not run times.
`--finish-window 08-10,20-22` adds the run counts that, installed at `--start` (`HH:MM` in `--tz`, or
an ISO time; default now), end inside a window: the most runs per window occurrence, longest first, up
to `--max-hours` (default 60). The text shows the two longest; `--json` lists every fit. Cap the answer
by the runs left on the blueprint yourself. `--tz` takes an IANA zone such as `Europe/Helsinki`, which
follows daylight saving, or a fixed offset such as `+03:00` / `UTC+3`, which does not (default UTC).
IANA names need a time zone database: Linux and macOS have one, Windows does not unless the optional
`tzdata` package is installed (`pip install tzdata`) - without it, use the offset. `--times` has no `--group` or `--csv` form.

`--since WHEN` lists what was installed since a moment, the question "did I miss anything" asks after
a round of jobs. `WHEN` is `HH:MM`, the most recent such time in `--tz` (default: the operation file's
`planning.timezone`, else UTC; so `08:00` at 07:30 means yesterday 08:00), or an ISO timestamp. It reads
finished jobs too, since a short job may already be delivered, and keeps the jobs whose `start_date` is
at or after `WHEN`. Rows are grouped by installer, activity, facility, product and runs, with the count,
the first start and last end in the zone, the status (`2 delivered, 1 active` when they differ) and, in
`--json`, `installer_id`, `product_id`, `facility_id`, `blueprint_ids`, and `start`/`end` in UTC. The
facility is the operation file's name for it when its `location_id` is in the file. Each owner's header
gives the time of the jobs data (ESI caches it about five minutes, so a job installed a minute ago may
not be in it yet). `--site NAME` reads the site's characters (a corp site: the corporation). `--since`
has no `--times`, `--slots`, `--group` or `--csv` form.

`--times --site NAME` answers "how long does a run take where this site does that activity", for a site
of the operation file whose activities may run in different facilities (`sites.NAME.facilities`). It
reads the site's characters (a corp site: the corporation, through its first reader). Per activity,
product and installer it reports the median measured at the site's facility for that activity. Without
such jobs it converts the median from another facility of the operation file that has a `location_id`:
hours x that activity's time multiplier at the site's facility / the one at the source (the source with
the most jobs wins); the table marks those `~` and `--json` names the source in `scaled_from` (`null` when
measured in place). A product measured only somewhere the file does not know is not scaled at all: its
rigs are unknown, so the row is left out and listed under "no time" (`unscaled` in `--json`). A site
facility without a `location_id` is an error. Window fits use the final hours. `--site` and `--facility`
do not combine.

Corporation `jobs`, `blueprints`, and `inventory` output reports the ESI document's
`Last-Modified` and `Expires` times as an “as of / next refresh” line. JSON carries the two ISO
timestamps as fields; CSV sends the line to stderr. When the optional jobs consent is available,
asset and blueprint views also warn when a delivered job is newer than the snapshot, because that
job's output cannot be present in the displayed numbers yet.

## `can-build` — jobs allowed by stock and skills

```bash
neworks can-build --corp --division T2-Prod
neworks can-build --corp --type "Shield Hardener" --builders "Ada,Vela"
neworks can-build --corp --json
```

The command considers idle blueprint groups, sizes one job from the copy's remaining runs (or the
SDE production limit for an original), applies that blueprint's ME with the same per-job rounding as
`build-cost`, and caps the result by copies held. Stock comes only from the blueprint's own station
and hangar division. When a binding material is elsewhere, the note names both quantities and tells
the operator to change the job's **Input Material Location**. Each selected stored builder is checked
against the recipe skills carried by the local SDE snapshot; an older snapshot reports skills as
unknown and names `update-data`. No market endpoint is read.

## `industry status` — one operational snapshot

```bash
neworks industry status --corp --json
neworks industry status --corp --materials watch-list.txt
```

`--corp` reads each corporation's jobs, blueprint inventory, assets, divisions and open
orders once even when several stored characters belong to it; slots remain per character.
Without `--corp`, the snapshot reads personal documents. The UTF-8 materials file lists one
exact type name per line, adding those types to the recipe-derived stock watch. Stock and
can-build counts are local to the blueprint's station and division; `CorpDeliveries` at
other stations are separate, not installable stock. JSON includes numeric ids beside names,
quantities, section availability and `Last-Modified`/`Expires` for each available document.
Unknown slot budgets and can-build counts remain `null`; missing optional consent supplies
an actionable hint and never invents a zero. Delivered jobs newer than assets produce a
staleness warning; delivered copying and invention jobs newer than the blueprints say that the
copies they produced are not listed yet.

Blueprints are cached about an hour and jobs about five minutes, so a copy used by a job delivered
after the blueprint snapshot would still show with its pre-job runs. The snapshot therefore reads
finished jobs too (personal ones as well as the corporation's) and takes each such job's runs
(manufacturing runs, invention attempts) off the copy it used: a copy left with no runs is dropped,
the others keep the runs they have left. Originals are never changed. Each owner's JSON has
`blueprint_adjustments: {"dropped": n, "reduced": m}`, and when either is non-zero the text and
`warnings` say `n copies dropped and m reduced: used by jobs delivered after the blueprint snapshot
(HH:MM UTC)`. Slot counts and the ready and running jobs never count a delivered job.

Each blueprint row says what it can do, from the local SDE data (`update-data`): `activities` lists
the industry activities (`manufacturing` or `reaction`; an original adds `copying`,
`material efficiency research` and `time efficiency research`; a copy with an invention recipe adds
`invention`, which needs a copy), and `invents` names the items its invention yields (the T2 products;
on an original, what its copies invent). A blueprint the data does not know has two empty lists.

## `blueprints` — originals and copies

```bash
neworks blueprints --corp --idle             # copies and originals not tied to a running job
neworks blueprints --corp --copies --type "Shield"
neworks blueprints --corp --group-by division --json
```

Blueprint originals and copies with runs, ME/TE, location and count, grouped by type (or `--group-by
division`; `--items` gives one row per item). `--idle` removes copies tied to current jobs (needs the
`jobs` consent too), `--copies` / `--originals`, `--type` and `--division` filter, and corporation
output names hangars with the `divisions` consent and flags a stale snapshot.

## `job-board` — one screen for planning the next jobs

```bash
neworks job-board                               # the operation file's default site
neworks job-board --site home --start 21:30     # run counts for jobs installed at 21:30
```

The board the `industry-jobs` agent skill plans from: slots, ready and running jobs, idle copies by what
they can do, run counts that end inside the finish windows (measured hours per run, as `jobs --times`),
and the inputs per facility. On a personal site it also checks the shared pool (`--no-pool-check` skips
it). The site, time zone and finish windows come from the [operation file](operation.md); `--tz`,
`--finish-window`, `--max-hours` and `--max-invention-hours` override them.

## `build-cost` — what manufacturing an item costs right now

```bash
neworks build-cost Hound                       # one install, ME 0 top / ME 10 components, shopping at Jita 4-4
neworks build-cost Hound --runs 5 --me 10      # five installs on a researched blueprint
neworks build-cost Hound --component-me 0      # the component blueprints are unresearched too
neworks build-cost "Fernite Carbide"           # a reaction: 10,000 units a run, no ME or TE
neworks build-cost Hound --build "Plasma Thruster"    # force one component to be built anyway
neworks build-cost Hound --buy-all             # never run a component job; buy everything
neworks build-cost Hound --hub amarr           # shop at another trade hub's station
neworks build-cost Hound --system Amarr        # bill the install in Amarr, still shop at Jita
neworks build-cost Hound "Plasma Thruster"     # several products, off one set of order books
neworks build-cost Hound --brief               # table, totals, verdict, essential warnings
neworks build-cost "Small EM Shield Reinforcer II" "Medium Core Defense Field Extender II" --te 6 \
  --system Jita --sell-at jita --seller Somecharacter --brief     # rank lines by ISK per job-hour
```

No login and no consent: the recipe comes from the local SDE snapshot — shipped in the package and
refreshed by `update-data` (the bundled one is build 3538132, 4952 blueprints covering 4943 distinct
products, including each activity's required skills) — and the prices come from public order books,
so this runs on a machine that has never seen SSO. Nothing about the recipe is estimated: it is
CCP's own material list for the blueprint that makes
the type, with that blueprint's own material efficiency applied to the quantities.

`--sell-at HUB --seller CHAR` adds what selling the output would earn: a list price at that hub station's
best sell, the net per unit after that character's sales tax and broker fee, the margin against the
build's cost per unit, per job and per job-hour, and how many days of the region's 30-day volume the
job's output is. The seller's tax and fee rates come from a plugin hook (`trade.seller_terms`); without
a plugin that provides it, the net and the margin stay empty and the output says why. Several products
print one table ranked by ISK per job-hour, which is how lines compete for
manufacturing slots. The job-hours are the blueprint's base time at `--te`; skills and facilities make
real jobs shorter, so pass `--hours-per-run` with the measured figure from `jobs --times` when ranking
matters. It is a marginal figure: a copy's invention cost is sunk and not in it. JSON adds a `sale`
object per product; `--csv` stays material rows only and is refused with `--sell-at`.

`--brief` retains the material table, totals, buy-versus-build verdict, and any warning that makes
the number incomplete or stale. It omits explanatory footnotes, request counts, and the scope
paragraph; one compact `scope:` line appears at the end, once even when several products are named.

```text
$ neworks build-cost Hound
Hound (id 12034) - 1 unit from 1 run, at ME 0 / TE 0, components at ME 10, blueprint 12035
material                               qty  buy/u       build/u     source  cost          surplus
-------------------------------------  ---  ----------  ----------  ------  ------------  -------
Fernite Carbide Composite Armor Plate  300  5,168.00    5,505.37    buy     1,550,400.00  0
Nanomechanical Microprocessor          180  45,240.00   43,505.85   build   7,831,053.22  0
Ladar Sensor Cluster                   60   18,410.00   18,981.93   buy     1,104,600.00  0
Electrolytic Capacitor Unit            60   43,350.00   43,135.18   build   2,588,110.90  0
Morphite                               38   17,790.00   -           buy     676,020.00    0
Construction Blocks                    30   10,200.00   -           buy     306,000.00    0
Plasma Thruster                        30   34,970.00   30,020.23   build   900,606.79    0
Deflection Shield Emitter              15   30,690.00   30,024.98   build   450,374.65    0
Nuclear Reactor Unit                   6    106,900.00  98,169.72   build   589,018.35    0
R.A.M.- Starship Tech                  3    799.00      22,610.21   buy     2,397.00      0
Breacher                               1    509,400.00  485,204.50  build   485,204.50    0
  A component job runs in whole runs, so the build column charges every run needed to
  cover the quantity above: a blueprint that yields more than the recipe wants leaves
  real surplus behind - product you own and could sell, not waste.
totals:
  material cost   16,483,785.40 ISK
  EIV             12,165,171.20 ISK  (base quantities x ESI adjusted price; ME does not reduce it)
  job cost        2,613,078.77 ISK  = EIV x (0.1723 cost index + 0.0025 facility tax + 0.0400 SCC surcharge)
  total           19,096,864.18 ISK
  cost per unit   19,096,864.18 ISK
  job time        1d 09h
buy instead: cheapest ask 15,980,000.00 ISK, richest bid 15,020,000.00 ISK at Jita 4-4 (station)
  buying is cheaper by 3,116,864.18 ISK for 1 unit (build 19,096,864.18 vs buy 15,980,000.00)
```

`buy/u` is the cheapest standing ask in the scope this run shops; `build/u` is what one unit of that
material costs if you run *its* blueprint instead — its own materials at their asks plus its own install
fee. `source` says which of the two won, per material: the cheapest option is taken row by row, and a
dash under `build/u` means there is nothing to compare, either because no local blueprint makes that
material or because building it cannot be priced. So the table is not a list of what you must buy — it
is the build-or-buy decision made for every input, which is the part worth automating.

Two research levels are named rather than one, because one level described a build nobody actually
makes. `--me` is the level of the blueprint being run; the jobs feeding it run at `--component-me`,
which defaults to ME 10 — component blueprints are ordinarily owned BPOs their holder has had long
enough to take to the cap, while the top blueprint of a T2 item so often is an invented copy that
carries no research at all. Guessing the pessimistic end for components was the single largest
distortion this command made, because ME is charged against every material of every component and not
only against the top job's: on the run above, `--component-me 0` costs 19,509,454.53 against
19,096,864.18. Researching the *top* blueprint stays a separate knob — `--me 10` took the same item to
17,604,461.25 and flipped more rows from buying to building. Both figures were re-measured minutes
after the table above, so neither matches it to the penny; order books move between runs.

The three money lines are deliberately kept apart because they answer different questions. `material
cost` is the sum of the chosen options. `EIV` is CCP's billable value — base quantities times ESI's
adjusted price — and ME does not reduce it, which is why a researched blueprint cuts the material bill
and leaves the install fee alone. `job cost` prints its own arithmetic instead of one opaque number:
the system's industry cost index, plus the facility tax (`--facility-tax`, 0.25% for an NPC station),
plus the 4% SCC surcharge. Each has a different knob — `--system` picks the index, `--facility-tax`
picks the tax, and the surcharge is CCP policy (see [Limitations](limitations.md)).

Substitution goes exactly one level deep, and that boundary is a request-budget decision, not an
oversight: each component's own materials are priced from the books this run already opened, so the
second level is free, while a third would need fresh books for every material of every component.
Measured over all 4943 products in the snapshot, one product needs a median of 8 distinct types priced,
p90 19, and 71 at the worst (Vanquisher) — multiplying that out is how a cost estimate stops being
cheap enough to ask casually. The cold run above spent 30 requests: 26 order books, one
`/industry/systems` for the cost index, one `/markets/prices`, and two name lookups. A second run inside
the quote-cache window reads no books at all, on the same rule `inventory --value-at` uses — ESI's own
`Expires` vouches for a cached figure or it is refetched. Asking for several products together is
cheaper per product because a material two of them share is read once.

Forcing is allowed and is not free, which the output shows rather than hides. On this item the mixed
default (19,096,864.18) beats `--buy-all` at 19,656,945.77 and `--build-all` at 19,298,269.07; forcing
`--build "R.A.M.- Starship Tech"` lifts the total to 19,162,681.81 and leaves 97 surplus units of that
component behind. Surplus is real product, not waste — a component job runs in whole runs, so a
blueprint yielding more than the recipe wants leaves something you can sell, and the `surplus` column
says how much. Reactions are their own activity: `build-cost "Fernite Carbide"` runs 10,000 units per
run, has no ME or TE of its own to research — so `--me` changes nothing there while its components
still run at `--component-me`, and the heading says both — and is billed with that system's *reaction*
index rather than manufacturing's. Five products in the snapshot are made by more than one blueprint;
the lowest blueprint id is used and the others are reported as alternatives rather than silently
averaged.

`--system` bills the install and nothing else — it does not move the shopping, so `--system Amarr` with
the default scope means buy at Jita, install in Amarr. That distinction matters because component jobs
are billed at the same index as the top job, so the system you name changes the material line too.
`--region` needs `--system` alongside it and refuses without one: a region has no single cost index, and
picking a system for you would be inventing a number. A named system whose industry document publishes
no manufacturing index is refused as well — with the note that only the tax and surcharge would apply —
after the books have been read, since nothing else about the run was wrong.

A material with neither an ask in scope nor a published price is reported as `-`, kept out of every
total rather than counted as free, and takes the per-unit figure down with it: `cost per unit` prints a
dash with the reason beside it, because a per-unit number with a hole in it is not something to quote to
another person. The notes under the table name the type and say that the install fee understates its
share, since EIV cannot include what has no adjusted price either.

Refusals exit 1 and ask for nothing: no local blueprint makes the requested type (the message names
`neworks update-data`, which is the only thing that can fix it), `--me` or `--component-me` outside
0..10, `--te` outside 0..20, `--build-all` together with `--buy-all`, and `--region` without `--system`.
Machine-readable output keeps the decision rather than only its winner: every material row carries a
`build` object with the blueprint id, runs, units, surplus, its own `material_cost` / `job_cost` /
`total` and per-unit cost — even on a row that was bought, so a script can see the option that lost and
by how much — or `null` where no blueprint makes it. Note that `build.unit` is the cost per unit
*produced* by that component job while the table prints cost per unit *needed*, which differ whenever a
run overshoots the recipe. Each product object reports the levels its money was priced at as `me`,
`te` and `component_me`, and `--csv` carries the same three columns in that order. Unpriced types appear
under `unpriced`, and `--csv` writes one row per material to stdout with every note on stderr, so a
header a script already reads keeps meaning.

## `restock` — what the next builds still need, and what to buy for them

```bash
neworks login --scopes assets              # once; corporation hangars also need the Director role
neworks restock --at "Jita IV - Moon 4 - Caldari Navy Assembly Plant" --char Somedirector \
  --hauler Somehauler --cargo 5000 \
  "Medium Core Defense Field Extender II=90" "500MN Microwarpdrive II=37:7" \
  --invent "Small Core Defense Field Extender II=140:Augmentation" --extra "Augmentation Decryptor=500"
```

Demand comes from the local SDE recipes: a build line `PRODUCT=UNITS[:ME[:RUNS]][@CHAR]` is costed as
the jobs it would really be installed as (RUNS per job, by default the blueprint's maximum or, for an
invented T2 copy, the most runs invention can give it; ME rounding per job). On a personal site (`--site`,
see `operation`) `@CHAR` names the installer and the plan lists the trades that supply them; `--invent
T2PRODUCT=ATTEMPTS[:DECRYPTOR]` adds the invention datacores per attempt and, when named, one decryptor
per attempt; `--extra TYPE=QTY` is a buffer bought in full on top of stock. Materials are direct inputs
only - a component you build yourself is its own line.

Stock is netted in this order: the corporation's hangars at `--at` (on site, including items inside its
office and containers); what the corporation and `--hauler` already own at the hub (`pick up`, no ISK);
and the hauler's personal hangars elsewhere in the build station's solar system (`move` into the
corporation hangar). The rest is bought at the hub's lowest sell (`--hub`, default jita). With `--cargo`
the hold is filled in priority order - inventions and extras first, then build lines in the order given -
and the last line that does not fit is cut to the units that do (stock on site covers the first units for
free, so the cut is searched, not scaled). The text ends with a Multibuy block, the pick-ups, the moves
and what the trip leaves out; `--json` has every line and material with its volume and ISK.

**A personal site with a facility per activity** (`sites.NAME.facilities`, see `operation`): without
`--at`, build lines are netted against the site characters' stock at the manufacturing facility and
`--invent` lines against their stock at the invention facility; each character's assets are read once.
What a site character holds at the *other* facility is not bought: it goes to `move`, with a note line
`move between site facilities: ITEM QTY at FACILITY, held by CHARACTER QTY`. Transfers are planned where
each job is installed and carry the facility (`at`). `--json` keeps `build_station` (the manufacturing
facility) and `holdings` (stock there), and adds `stations` (`manufacturing` and `invention`, id and name)
and `holdings_by_station` (`{station id: {character: {type id: qty}}}`). With `--at`, one location covers
everything, as before. A corp site with per-activity facilities needs `--at`.

**Price check.** Judging what the trip buys against each material's normal price needs a plugin that
provides the `trade.restock_price_check` hook. Without one, `restock` buys at the lowest ask and notes
that the check was skipped; `--no-price-check` drops that note.

## `production` — continue a round, supply it and replenish its sales

`production round save/check` persists the intended jobs and compares them with observed installs.
The remaining jobs share one bulk blueprint/material pool, passed from installer to installer.
`production supply` forecasts shortages and purchase deadlines; `production replenish` recommends
listing or hauling existing stock before building more. `production scan` compares product economics
with manufacturing and invention capacity. `production facility` previews and records dated facility
settings for historical ledger costing.

See [Production workflow](production.md) for examples, the round JSON format, freshness rules and
costing assumptions. Run `ledger sync` after upgrading to create the new tables; reports do not migrate
a ledger opened read-only. Plans and handoff confirmations remain private in the ledger.
