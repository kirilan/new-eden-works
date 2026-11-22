# The operation file

An optional file, `operation.json` in the config directory, describes where the operation works. Without
it every command keeps its built-in behaviour (NPC station rates, the five trade hubs, corporation reads
where a command asks for them). Print a starting point and check what is filled in:

```bash
neworks operation template > ~/.config/new-eden-works/operation.json   # then edit it
neworks operation show                                             # flags values still unknown
neworks operation playbook                                         # this installation's own rules
```

- **Sites** name a facility plus the characters working it. `owner: corp` keeps stock in the
  corporation's hangars, read through `readers` (characters with the Director role). `owner: personal` is
  for building in someone else's structure without corporation roles: every character in `characters`
  installs from their own hangar, and materials move between them by trade. One operation can have both,
  for example a high-sec corporation and a null-sec structure during a move. `default_site` is what
  commands use when no `--site` is given.
- **A facility per activity:** `facility` is where a site runs everything; `facilities` names another
  facility for single activities, e.g. `"facilities": {"manufacturing": "example-sotiyo"}` for a site that
  invents in one structure and builds in the next system. Keys are `manufacturing`, `reaction`,
  `invention`, `copying`, `research_material`, `research_time`; values are facility keys.
  `operation show` prints such a site's facility as `example-raitaru; manufacturing: example-sotiyo`.
- **Who sells and who holds the stock:** a site's optional `seller` is the character who lists its goods;
  `pool_holder` (personal sites only, one of its `characters`) is the character who holds the shared
  stock that builders take turns with.
- **Facilities** carry the system, the structure's `location_id`, the owner's `tax_pct`, and the material
  and time bonuses. Either state them (`material` and `time` per activity, as multipliers) or derive them
  from what you know about the structure: `"structure": "raitaru"`, `"security": "null"`,
  `"rigs": {"manufacturing": {"me": "t1", "te": "t1"}}`. A derived bonus is
  (1 - hull bonus) x (1 - rig bonus x security multiplier). The security multiplier is 1.0 in high-sec, 1.9 in
  low-sec and 2.1 in null-sec. A Raitaru with T1 manufacturing rigs in null-sec comes to a material
  multiplier of 0.9484 and a time multiplier of 0.493. Engineering complexes' job-cost reduction is not
  modelled, and rig bonuses are assumed to cover every product built there.
- **Hubs** add markets beyond the five built-in ones: `location_id`, `region_id`, optional `system_id`,
  `label` and `reader`. A player structure's id (above the int32 range) is priced from its own market,
  `/markets/structures/{id}`. The regional feed carries none of a structure's sell orders. Reading the
  structure market needs a character with docking access there who granted
  `neworks login --scopes structure-markets`. `reader` is tried first. A refusal is an error that
  names the fix, never an empty book.
- **Freight** routes (`"jita->x-hub"`) price a courier contract: `per_m3`, `collateral_pct` and `min`.
  A site's `freight_in` and `freight_out` name the routes its supplies and products travel.
- **Planning and selling settings** (all optional, for planners and selling helpers):
  `"planning": {"timezone": "Europe/Helsinki", "finish_windows": "08-10,20-22", "never_build": [...],
  "t1_copies": "invent"}` (`t1_copies`: what a T1 copy is usually for, `invent`, `build` or `both`, the
  default) and `"selling": {"keep": [...]}` (parts of item names never offered for sale). A bad time zone
  or window range, or an unknown key, is an error naming the field. Facilities may also carry a `label`
  (the structure's in-game name), used in messages.

**Playbook.** The agent skills hold workflows that suit any operation; an installation's own rules (who
builds what, which lines, past decisions) are plain Markdown files outside the repository, one per skill,
in `<config dir>/playbook/` (or `$NEWORKS_PLAYBOOK`), where they cannot be committed by accident.
`neworks operation playbook` lists them with their first heading, size and date (or says where to
create one); `operation playbook NAME` prints `NAME.md` after a line with its path and date, and a missing
file is one line, not an error; `--path` prints only the path.

Commands that use the file:

| Command | What it does with the file |
|---|---|
| `build-cost --facility NAME` / `--site NAME` | Cost the job there (a site: its `manufacturing` facility): the system's index, the tax, the material bonus, and job time with the time bonus. Explicit `--facility-tax` and `--material-multiplier` still win. Without `--facility`, `--site` or `--system`, the operation file's default site builds the job, so its structure and rig bonuses are always in the cost; `--system NAME` alone costs it with no facility. Build lines may also ask for the runs an invented T2 copy holds (10 on a rig copy invented with Augmentation), not only the T2 blueprint's own limit. |
| `build-cost --hub/--sell-at KEY` | Price at a configured hub, including a structure. |
| `restock --site NAME` | Build at the site's manufacturing facility (inventions at its invention facility). On a personal site, stock is read per character; `PRODUCT=UNITS[:ME[:RUNS]]@CHAR` and `--invent ...@CHAR` assign the installer. Output lists the **transfers** that supply each installer, what to hand them from the haul, and the courier cost on `freight_in` (or `--freight ROUTE`). |
| `can-build --site NAME` | Applies the material bonus of the facility where the site builds (its `manufacturing` facility). Without `--site`, a blueprint that sits in a configured facility gets that facility's bonus. On a personal site it adds `with trades`, the jobs possible once other site characters hand over what they hold there, and the trades that make them possible. |
| `jobs --times [--facility NAME]` | Median hours per run are always kept per facility, so a structure's rigged times never mix with NPC-station history. |
| `jobs --times --site NAME` | Hours per run where the site does each activity (`facilities` per activity), converting times measured at another facility of the file by the time multipliers and never scaling from an unknown one. |
| `dashboard` | Reads industry status for every site: a corp site through its reader, a personal site from its characters' own snapshots. |
| `ledger sync` | Reports a corporation that refuses every section on roles (building in someone else's corporation) as one expected line. The members' personal jobs, orders and wallets are booked as before. |
