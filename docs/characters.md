# Characters and skills

Skills, training queue and clone state, plus attributes, standings, location and implants, priced
training plans and Skill Extractor math. These commands come from the `characters` plugin.

## `skills` — the main view

```bash
neworks                                 # every stored character, text tables (same as: neworks skills)
neworks skills --char Somecharacter
neworks skills --filter omega        # all | alpha | omega (text table only)
neworks skills --sort sp             # name | level | sp
neworks skills --trained-only        # drop the queue section
neworks skills --week                # append SP gained over the last 7 days
neworks skills --json > skills.json
neworks skills --csv > skills.csv
```

The text block per character shows name/id, race, alpha clone grade, inferred clone state with its
confidence, up to three evidence lines and any warnings, total/unallocated SP, the training queue
(`TRAINING` / `queued` / `done` / `BLOCKED`), then the trained-skill table with per-skill alpha cap
and access column. Rows marked `*` finished training but ESI has not reflected them yet — they apply
on next in-game login. Never-trained prerequisite skills that ESI also lists are filtered out of the
table and out of CSV alike.

The queue's `level sp` column is SP into the level being trained, out of what the level costs
(`249.5K/256.0K 97%`), interpolated from the three SP figures ESI publishes per item —
`level_start_sp`, `training_start_sp` and `level_end_sp`. It is deliberately not the fraction of
`start_date`..`finish_date` that has elapsed: EVE restamps `start_date` on the active item every
time the queue is rearranged, so that fraction describes the current sitting rather than the level,
and a level 95% trained would read as 11% an hour after a reorder. A queued or blocked item shows
`-` — it has trained none of its level yet, and the timing column says when it will.

Output-format precedence: `--watch` wins over everything; otherwise `--csv` wins over `--json`.
`--filter`, `--sort` and `--week` shape the **text** table only — JSON always contains every row and
every field, CSV always emits all trained (or pending) rows in source order.

`skills --json` prints a single object for one character and a JSON array when several characters are
fetched.

CSV columns (`skills --csv`, one header row across all characters):

```text
character_id, character_name, skill_id, name, trained_level, active_level, sp,
alpha_cap, access, restricted, pending_completion, unknown_data
```

## `summary` — fleet-wide one-liner

```bash
neworks summary
```

One row per fetched character (clone state + confidence, total SP, queue items, time left on the
currently training item) plus a `TOTAL` row. Characters that fail to fetch are reported as
`warning: skipped ...` on stderr and do not abort the command.

## `attributes`

```bash
neworks attributes --char Somecharacter
```

PER/INT/MEM/CHR/WIL and remaps available with the last remap date. These are **effective** values —
fitted implant bonuses are already inside them (verified against live training, 2026-09-13), which
is worth knowing before spending a remap on them. No extra consent is needed; a character whose
stored consent somehow lacks the skills scope gets a hint line instead of data.

## `standings`, `travel`, `implants`

```bash
neworks standings --csv > standings.csv
neworks travel                     # current location, home, jump clones + their implants
neworks implants                   # implants in the active clone (one row per fitted instance)
```

All three accept `--char` and `--csv`, and each needs its own consent (`--scopes standings`,
`location` and/or `clones`; see [login](login.md#adding-characters-extra-consent-logging-out)). In CSV
mode any consent hint is written to **stderr** so stdout stays machine-readable.

## `plan` — training plan (one character)

```bash
neworks plan --char Somecharacter "Astrogeology:5" "Nanite Operation"
neworks plan --char Somecharacter --rate 3600 "Local Industry:4"
```

Target syntax is `SKILL[:LEVEL]`, level defaulting to L5. Names resolve against the **full SDE
skill catalog** installed by `update-data` — exact match first, then a unique substring; an
unknown or ambiguous name is an error listing what it found, and a missing catalog tells you to
run `neworks update-data`. Skills the character has never trained are planned just fine. A
repeated target means its deepest level.

What the plan does:

- **Prerequisites expand automatically**, recursively, at the highest level any target needs
  them; a shared prerequisite appears exactly once, and row order guarantees every skill trains
  after its prerequisites (foundations first, stable ties by name). The `why` column says whether
  a row was requested or which skills it unlocks.
- **Costs are rank-based and exact**: cumulative SP at level L is `round(250 × rank ×
  2^(2.5·(L−1)))`, the canonical CCP table. Attributes never change the SP amount, only how fast
  it accrues — there is no attribute-sum estimate any more.
- **Start levels are queue-aware**: an existing queue entry that already raises a skill sets the
  item's start level (marked `*` in `now`), and a target or prerequisite already covered by the
  trained or scheduled level costs nothing and is reported as covered.
- New items are scheduled after the existing queue drains; that backlog finish time is printed.
- The rate is computed **per skill** from the character's own attributes — CCP's formula,
  `(primary + secondary/2) × 60` SP/hour for an omega clone, half that for an alpha. Because EVE
  prices speed by attribute pair, one plan can carry several exact rates: Command Center Upgrades
  and Planetology each get their own number instead of the user running `plan` twice with two
  `--rate` values. One `rate:` line prints per pair used, and every row is timed by its own skill's
  pair. The endpoint reports **effective** attributes — fitted implant bonuses are already included
  (verified against live queue timings, 2026-09-13). The full precedence:
  1. `--rate <SP/hour>` prices every row with one hand-picked number;
  2. the character's attributes, when its stored consent covers them and the lookup answers;
  3. a live `TRAINING` queue item (ground truth for the skill being trained) — reached when the
     attributes cannot be fetched: an old login predating the skills consent, or a failed lookup,
     which is warned about on stderr;
  4. the slope of local SP history over 7 days;
  5. an error naming what is missing — including `neworks login --scopes attributes` when that
     character's stored consent is what lacks it.
  The live measurement spans `start_date` to `finish_date` and is paired with ESI's
  `training_start_sp`, the SP held at that `start_date`: EVE restamps `start_date` on the active
  item every time the queue is rearranged, so measuring the level's whole SP against it would
  report a rate several times too fast. An item ESI publishes no `training_start_sp` for is skipped
  rather than guessed at, and the SP history answers instead. Flat or extraction-dipped history is
  deliberately rejected as a rate.
- Notes flag targets above the character's alpha cap, omega-only skills, and skills CCP no longer
  publishes.

Remaining caveats, stated by the tool itself: when the plan falls back to one calibrated rate — no
`--rate` and no fetchable attributes — that single number prices every row, so plans spanning
several primary/secondary attribute pairs are estimates for rows driven by other attributes (the
output names the pairs involved); remaps or implant changes during training shift real time;
implants are never modeled into future levels. A prerequisite the local catalog has no
row for, or a cyclic prerequisite chain, is an error naming the skill or the cycle.

## `extract` — Skill Extractor math (one character)

```bash
neworks extract --char Somecharacter
```

Reports allocated vs unallocated SP, how many 500k SP extractors the character can run while keeping
the 5,000,000 trained-SP floor (5,500,000 allocated SP minimum to extract at all), the approximate
re-training days per extractor at the calibrated rate, and what re-injecting here would yield given
the injector tiers (500k below 5M total SP, 400k to 50M, 300k to 80M, 150k above). Queued/training SP
is not extractable. These are dated constants verified against CCP on `2026-09-05`; after ~180 days
the command prints a staleness warning instead of the "rules per CCP" line.


`plan` and `extract` require `--char` when more than one character is stored — they are single-character
operations by design.
