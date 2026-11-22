# Items, systems and game data

Look-ups that need no login: what an item is (`types`), what a solar system is like (`system`), and the
local copy of CCP's static data (SDE) that recipes, skills and planets come from (`update-data`).

## `types` — what an item is and how big it packs

```bash
neworks types Tritanium 34 "Medium Core Defense Field Extender I"
neworks types - < cargo.txt          # one name per line, or the in-game name<TAB>quantity paste
neworks types Tritanium --json
```

Per argument: the type id, the exact name, group, category and packaged volume in m3. Names match
exactly, case aside (one batched `/universe/ids` request for all of them); digits are type ids. An
unknown name or id is reported on its own line (`not a type: ...`; on stderr with `--csv`) and makes the
exit status 1, while the others still print. With `-` and quantities, each row echoes the quantity and
adds `m3 total`, and a total line follows. `--json` gives `types` rows (`input`, `type_id`, `name`,
`group`, `category`, `packaged_m3`, `quantity`, `total_m3`), `unknown` and `total_m3`; `--csv` the same
columns.

## `system` — security, planets and jump distance for candidate systems

```bash
neworks system Rens Outuni Akidagi Balle      # one row per system
neworks system Rens Outuni Akidagi --route jita # + jumps to a hub
neworks system Akidagi --route jita --flag secure   # the safe road's length instead
neworks system 30045342 --json                  # machine-readable, notes included
```

No login: security status, constellation and region come from public ESI, the planets from the local
census `update-data` builds. It exists because picking a staging system for a colony used to mean three
throwaway scripts — one for exact security, one for planet types, one for jump counts — and because the
figure EVE prints is not the figure that decides anything.

```
system   security  shown  class    jumps to Jita   region       planets  planet types
-------  --------  -----  -------  --------------  -----------  -------  -------------------------------------------------------------------------
Rens     0.8946    0.9    highsec  15 (secure 25)  Heimatar     9        Temperate x4, Barren x2, Gas x2, Ice x1
Outuni   0.7342    0.7    highsec  4               The Forge    12       Gas x5, Barren x2, Lava x1, Oceanic x1, Plasma x1, Storm x1, Temperate x1
Akidagi  0.4449    0.4    lowsec   10 (secure 12)  Black Rise   10       Barren x3, Temperate x3, Plasma x2, Gas x1, Oceanic x1
Balle    0.4609    0.5    highsec  12 (secure 13)  Sinq Laison  8        Gas x5, Barren x2, Ice x1

  Akidagi 0.4449 -> shows 0.4, Balle 0.4609 -> shows 0.5 sit within 0.05 of a class line, and EVE only
  ever shows the rounded figure; these rows span 0.0160 of true security and are not all one class
```

(From a run with `--route jita` on 2026-11-11 against live ESI and SDE build 3538132; the other notes are left out, the class-line note is wrapped.)

**Security is classified on the true value, not the printed one.** CCP's own guide
([developers.eveonline.com/docs/guides/system-security](https://developers.eveonline.com/docs/guides/system-security/))
says the client rounds to one decimal — away from zero, with one exception: any positive status below
`0.05` shows `0.1` rather than `0.0`, because a system that reads 0.0 is shoot-on-sight space. ESI's
`securityClass` field is documented as having an unknown meaning and is never read here. That rule is why
Akidagi (measured 0.4449388384819031 on 2026-11-11) is **lowsec** while Balle, sixteen
thousandths higher at 0.4608891010284424, is **highsec**: `x >= 0.45` is highsec, and both
systems print a displayed figure that hides it. Any run whose rows come within `0.05` of a class line says
so under the table, quoting the true spread — the mistake this command was written to prevent.

**Planets are counted from the local census**, so the printed total always equals the breakdown beside it,
and types nobody can colonise still get named: 116 systems in build 3494416 contain a Shattered planet, and
`pi` has nothing to say about those. ESI's own planet count is fetched anyway (it is already in the system
record); when the two disagree — the census is a build, ESI is now — the run says which said what, quoting
the census build, and `--csv` keeps both figures as their own columns. A system with no census row prints
`-` rather than `0`, because 402 of the SDE's systems genuinely have no planets: absence is an answer here.

**`--route HUB`** resolves the hub name to a solar system (`jita`, `amarr`, and anything else ESI will
resolve) and prints jump counts. `shortest` is what a pilot plans with; `secure` avoids the space a hauler
cannot fly in, so both are fetched and the cell reads `11 (secure 20)` for Rens to Amarr, where the safe road is nearly twice as long. Equal counts over
different paths are still disclosed — two roads of the same length are not the same road. `--flag insecure`
has no meaningful sibling, so nothing is compared and nothing is requested for it. One link ESI will not
route costs that cell a dash and a note quoting ESI's reason; the rest of the table still prints. Routing a
system to itself answers zero jumps without asking.

The planet column needs the census: with none installed the command refuses with
`no local planet census - run: neworks update-data` and makes no request at all. Security status has
exactly one source, so a system that will not describe itself ends the run with an error naming it rather
than a row of dashes.

## `update-data` — the game data from the official SDE

```bash
neworks update-data              # latest build (~100 MB download)
neworks update-data --build 3494416   # example: pin a known specific build
```

Downloads the official JSONL SDE zip from `developers.eveonline.com`, extracts clone grades, bloodline
races, the full skill catalog (name, rank, attributes and prerequisites for every catalogued skill),
the material list of every blueprint — manufacturing runs and reactions alike, keyed by the product each
one makes — and the planetary-industry chain: each planet type with what its extractors pull, every
recipe with its inputs, outputs, cycle time, plant class and the planets able to run it, the CPU and power
each structure draws, a command center's output at every upgrade level, and the customs-tax factors —
and every planet in New Eden grouped by the system it orbits — and the market type index, which maps each
of the 814 groups of market-listed types (and each of their 32 categories) to the types it holds — and
the invention document (each inventable blueprint's data cores per attempt, the blueprint it yields with
its runs and base chance, and the eight generic decryptors' modifiers, which `ledger` costs invention
with) — and atomically replaces eight files in the data directory
(`$XDG_DATA_HOME/new-eden-works`, `%LOCALAPPDATA%\new-eden-works\data` on Windows). That user copy takes
precedence over the snapshot shipped in the package, so you can refresh caps, the catalog, the recipes and
the planetary data without touching the checkout. `plan` is built on this catalog — without one it refuses
with `no local skill catalog - run: neworks update-data` — and `build-cost` is built on the recipes,
which is why a type that nothing makes locally ends by naming `update-data` instead of printing a table of
dashes; `pi` reads the planetary document and `system` reads the census. The census is deliberately its own
file rather than another section of `planet_industry.json`: it is 474 KB of per-system counts that `pi fit`
would have to parse before adding up eight integers, and it counts every planet type in the SDE — including
the ones nobody can colonise, which `planet_industry.json` has no name for. The whole download runs under `update.lock`, so two concurrent
runs cannot both pull ~100 MB and interleave builds, and each file is replaced atomically. `skills` warns
when the local snapshot is more than 90 days old (the age line also names the SDE build in use).

The invention document is its own file because a T1 blueprint's invention row would shadow its
manufacturing row in `blueprint_materials.json`, which every consumer reads as "the recipe this
blueprint builds". Decryptors come from dogma rather than a table in the code, restricted to group
1304 ("Decryptors - Generic"): 66 types carry the four decryptor attributes, most of them retired
racial decryptors and relic salvage that invention no longer accepts.

The market type index is its own file for the same reason: it costs 0.94 MB against a 153 MB `types.jsonl`,
and `pi` would have to parse all of it to answer a question it never asks. Only types that are both
published and carry a `marketGroupID` are in it — the SDE's own test for "this is something you can buy".
It is there for plugins that look types up by market group or category.
