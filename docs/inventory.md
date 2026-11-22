# Inventory

`inventory` names, places and values what your characters or corporation own (`--scopes assets`).

```bash
neworks login --scopes assets,structures   # once, per character, in the browser
neworks inventory                          # summary grouped by where the items are
neworks inventory --by category            # the same numbers, turned the other way round
neworks inventory --items                  # one row per item, most valuable first
neworks inventory --value-at jita          # value at the richest standing buy order there
neworks inventory --value-at "The Forge"   # …or across a whole region, not one station
neworks inventory --corp                   # corporation assets (director / Account-Manager role)
neworks inventory --json > holdings.json
neworks inventory --csv > holdings.csv     # footnotes go to stderr, so the pipe stays clean
```

ESI's asset rows are numbers — a `type_id`, a `location_id`, a `location_type` — and nothing else. This
command turns them into what each item is (name, group, category), where it actually sits, and what it
is worth on one of two labelled bases. Each owner gets a heading (`<name> (<N> asset rows)`), one table,
and a grand total under it; a character with nothing in their holdings gets `(inventory empty)` instead
of an invented zero.

**Three views.** The columns, exactly as printed:

```text
--by location (default)   location | category | types | units | value
--by category             category | location | types | units | value
--items                   item | group | category | qty | location | unit price | value
```

- **`--by location`** (the default) opens a section per root place — the station, player structure or
  open system that everything in it folds up into — and breaks that down by category. Each section ends
  with a `subtotal <place>` row; the table ends with the grand total.
- **`--by category`** is the same aggregation transposed: sections by category with a
  `subtotal <category>` row each, rows by place. The location cell carries the full nested path here, so
  a row reads on its own whichever way round the table is turned — and the TOTAL line comes out identical,
  because it is the same money.
- **`--items`** drops the grouping: one row per asset, most valuable first, with every unpriced row
  behind every priced one and names breaking the ties.

**Where an item really is.** A `location_id` can name a station, somebody's citadel, open space, or
another item — so the view walks the parent chain and prints it root-first:

```text
Jita - Mradd > My Freighter > Cargo Hold
```

That is a module in a container in a ship docked at a station, and each view reads a different slice of
it. The default `location` column and its section heading are the **first** cell — the place everything
under it folds into, which is why an item two levels inside a docked ship counts in that station's
subtotal. The location column of `--items` and of `--by category` is the **whole chain**. CSV's legacy
`location_name` stays the **last** cell — the container the item is physically in — with the full chain
beside it in `location_path`. Kinds are `station`, `structure`, `system` (loose in space), `container`,
`ship`, `other`. A structure this token may not see keeps a stable `structure <id>` cell and the run says
so once with the command that fixes it — a label that names the id, never a bare number.

**Names its owner chose.** A player-named singleton shows both halves — what they call it and what it is:

```text
Morning Bell (Rifter)
```

ESI answers `assets/names` with the literal string `"None"` for an item nobody ever named (measured: 18
of one character's 23 singleton rows), so that placeholder is dropped and the type name wins. Otherwise
most of a hangar would read `None (<Type>)`; only a player who really typed *None* as a ship name gives
anything up by that rule.

**`--json`** is one document: `generated`, `owner_kind`, `grouped_by`, the whole `value_basis` (key,
label, short label, scope, requests, priced and unpriced types, failed books, freshness, the alternative
listing figure, cached figures), `hints`, `warnings`, and per character its `character_id`, `name`,
`asset_rows`, `totals`, `groups` (the grouped view, subtotals included) and `items`. Every item carries
ids and names together — `item_id`, `type_id` + `type_name` + `name` (the display name), `custom_name`,
`group_name`, `category_name`, `quantity`, `singleton`, `flag`, `location_id`, `location_kind`,
`unit_price`, `value`, and `location_path` as objects with `id` / `name` / `kind` — so a reader never has
to resolve anything afterwards, or guess which of two names it is looking at. Prose stays off stdout:
hints and warnings are fields here, not lines.

**`--csv`** is always per-item and carries 19 columns. The nine this command has always published keep
their exact positions and meanings, and everything new is appended behind them — so a spreadsheet that
read the old header still reads the same thing out of the same column:

```text
character,item_id,type_id,item_name,quantity,singleton,flag,location_id,location_name,
group_name,category_name,custom_name,location_path,location_kind,price_basis,price_scope,unit_price,value,unpriced_types
```

`item_name` still means the name of `type_id`; a player's own label gets its own `custom_name` column
rather than quietly replacing it. `unpriced_types` is that owner's count of types this basis could not
price, repeated on each of their rows. An unpriced row leaves `unit_price` and `value` empty rather than
writing `0`.

**Two bases, because they answer different questions.** The money column always says which one produced
it.

- **Default — `ESI reference`.** One `GET /markets/prices` request prices every distinct type held:
  *"ESI's published reference price - a figure CCP publishes about an item, not an order anybody will
  fill"*. It uses `average_price`, falling back to CCP's industry `adjusted_price` only for rows with no
  average. **A reference price is not a quote**: nothing can be bought or sold at it, and it does not
  move when the market moves.
- **`--value-at HUB|REGION` — `max buy @ <scope>`.** *"the richest standing buy order at {scope} - what
  dumping the holding there pays right now"*. A hub (`jita`, `amarr`, `dodixie`, `rens`, `hek`) reads
  that station's orders only, because money you cannot reach is not a valuation; naming a region (exact
  name or id) widens past it. This costs one order-book request per distinct type the cache cannot
  answer, and the scope is resolved before any asset page is fetched — a mistyped hub should cost one
  lookup, not a haul. **`max buy` is what dumping pays, not what listing would raise**: from the very
  same rows the run also prints the free alternative — `listing the same holdings at <scope>'s cheapest
  standing ask would raise <X> ISK over <N> types` — so both figures are on screen and neither gets
  mistaken for the other.

**Unpriced is not worthless.** `-` in a money cell means this basis has no figure for that type; `0.00`
means ESI published zero, which is a price. Unpriced types are excluded from every total — row, subtotal
and grand total — counted out loud (`priced 4 of 5 distinct types held (…)`) and named there
(`no price on this basis, excluded from every total above (1): <Type Name>`). A book that never answered
is a third statement: `N of those books did not answer; their types are counted as unpriced above, not as
worthless`. The grand total therefore quotes only the units it actually priced, with the basis inside its
own label (numbers below are illustrative):

```text
TOTAL (max buy @ Jita 4-4): 1,234,567.00 ISK over 987 units of 42 distinct types; 3 more types held, none priced
```

When nothing on this basis has a figure it says `nothing priced on this basis (N distinct types held)`
rather than printing `0`.

**What it costs.** Two fan-outs hide behind this command, and telling them apart is the difference
between a slow tool and a broken one.

- **The type catalogue — paid once per new type, ever.** The first time this machine meets a type it
  fetches `/universe/types/{id}`, then one request per distinct group those types name, then one per
  distinct category those groups name. Measured on a real holding of 518 distinct types: **518 + 168 +
  18 requests ≈ 125 s cold**, and the same holding warm in **9 requests ≈ 3 s**. Types, groups,
  categories, station and system names never change, so they come off disk forever — a newly met type in
  an already-known group costs exactly one request. What is re-read every run is the personal half (the
  asset rows themselves, custom item names, token-visible structures), because those change the moment a
  ship is renamed or a citadel repacked.
- **`--value-at` — one order book per distinct type.** Measured on that same 518-type holding: cold
  **100.6 s / 518 requests**; an immediate repeat **14.1 s / 63 requests** (455 types answered from
  disk); `--items` straight after that **4.0 s / 18 requests** (500 cached). Books go through eight
  workers at roughly five a second, so the wait scales with how many distinct *types* you hold, not with
  how many items.

**The wait says what it is buying.** Before a book fan-out starts, stderr gets counts rather than a
progress bar:

```text
pricing 518 distinct types held: 518 order books to read, one per type; about 1m 38s at this size
```

with `(455 already priced from the last run)` inserted when part of it is cached — and, when nothing
needs fetching at all, `pricing 518 distinct types held: every figure already in the local quote cache,
so no order book is read`. The duration is quoted only above ten seconds: below that the wait needs no
explaining, and a notice about nothing teaches you to stop reading notices. `--json` drops it — those
counts are already fields in `value_basis` — while `--csv` still prints it on stderr, where every other
footnote for that mode lands too.

**The quote cache.** `--value-at` writes `quotes.json` in the cache directory: for each (region,
station/system filter, type) just the reduction of that book — `min_sell`, `max_buy` — plus the
response's own `Last-Modified` and `Expires`. Never the order rows themselves; about 85 KB for 518 types.
An entry is served only while its own stated `Expires` is still in the future, so a warm run never prints
a figure ESI has already disowned — and a figure reused from disk keeps the stamp ESI gave it: **a printed
total is stamped with the oldest contributing `Last-Modified`, cached or fetched**, so it can never be
described as fresher than its stalest input. The default reference basis does not go through this cache:
it is one document for the whole cluster, left to the transport's own `Expires`/ETag handling. Writes
merge under a lock and drop expired records, so two characters valued from two shells cannot lose each
other's figures, and a half-written or hand-edited file costs a refetch rather than a wrong price.
