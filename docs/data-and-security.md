# Where data lives, and how it is protected

Where New Eden Works keeps its files on each platform, what each file holds and how it is protected, and
`doctor`, which checks all of it.

## The four roots

One module — `newedenworks/paths.py` — decides where anything goes, and nothing else re-derives it. A
set and non-empty `$XDG_CONFIG_HOME` / `$XDG_CACHE_HOME` / `$XDG_DATA_HOME` / `$XDG_STATE_HOME` wins
on **every** platform, Windows included (an empty value counts as unset, and a pin is taken exactly as
given). With none set:

| Kind | POSIX default | Windows default | Why there |
|---|---|---|---|
| config | `~/.config/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\config` | Local **on purpose**: this is where `tokens.json` keeps live refresh tokens and an optional client secret, and `%APPDATA%` is what domain profile sync and OneDrive Known Folder Move replicate — credentials must not leave the machine that way |
| cache | `~/.cache/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\cache` | Regenerable — a roaming cache buys nothing but profile size |
| data | `~/.local/share/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\data` | The SDE copy can be re-downloaded by `update-data`, so it must not roam |
| state | `~/.local/state/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\state` | A watch state is machine-local by definition — the watchers, their baselines, their recorded alerts |

A missing `%LOCALAPPDATA%` falls back to the documented `AppData\Local` under the user profile;
only if even the profile cannot be located does a resolver return the POSIX-shaped path, because
naming the wrong tree beats naming nothing.

## The files

| File | Root | Contents | Sensitivity |
|---|---|---|---|
| `tokens.json` | config | Access + refresh tokens per character, granted scopes, client id/secret | **Secret — live credentials on both platforms.** Written `0600` where mode bits exist and private by inherited profile ACL on Windows; atomic replace, read-modify-write under `tokens.lock` so a running `--watch` and a manual command cannot corrupt each other's write |
| `config.json` | config | Client id/secret, optional `user_agent` | **Secret** when it holds a secret — written private, updated under `config.lock` |
| `sp-history.jsonl` | config | `{ts, char_id, total_sp}` rows, last 60 days | Non-secret, local-only SP history; pruned rewrites hold `sp-history.lock` |
| `watch-state.json` | state | Last queue observations per character, **the order owners being watched** under `owners` — one entry per `char:<id>` / `corp:<id>` with its open, pending and already-settled order ids — **and the colonies being watched** under `colonies`, one entry per character keyed `<planet_id>:<pin_id>` for each extractor with its `expiry_time` and whether it is still live (30-day retention on all three). Those halves are what make watch alerts fire exactly once | Non-secret; deleting it only re-announces whatever was in flight |
| `events.jsonl` | state | Watch alert history shown by `events` — training, order and planetary-extraction kinds alike, 365-day retention | Non-secret; append-only JSONL, always written in binary mode so no platform can rewrite its newlines |
| `endpoints.json` | cache | SSO discovery document, cached 24 h | Non-secret |
| `names.json` | cache | Resolved id→name cache (skills, stations, systems, item types), merged under `names.lock` | Non-secret |
| `types.json` | cache | The type catalogue `inventory` builds: per type id its name, group and category — groups and categories cached as their own sections, so a new type in an already-known group costs one request. `version`-tagged, merged under `types.lock` | Non-secret (public universe data); deleting it only re-buys the fan-out for ids this machine has not met since |
| `quotes.json` | cache | The reduction of every order book `inventory --value-at` read: `min_sell` / `max_buy` per (region, station/system filter, type) plus that response's own `Last-Modified` and `Expires`. Never the order rows. `version`-tagged, merged under `quotes.lock`; entries past their `Expires` are dropped on the next write | Non-secret (public order-book figures); deleting it only costs a refetch |
| `{clone_grades,bloodline_races,skill_catalog,blueprint_materials,planet_industry,system_planets,market_types}.json` | data | SDE snapshot from `update-data`; overrides packaged data. `blueprint_materials.json` is the one `build-cost` reads — every blueprint's activity and material list, keyed by the product it makes; `planet_industry.json` is the one `pi` reads — recipes, structure fitting costs, command-center output per upgrade level and customs tax, all keyed by type id; `system_planets.json` is the one `system` reads — every planet in New Eden counted per solar system by planet type (68,407 planets over 8,088 systems in build 3503375), with a name for each type it met; `market_types.json` is the market type index — every market-listed type under its group and its category (19,551 types over 814 groups and 32 categories in build 3494416) | Non-secret |
| `blueprint_invention.json` | data | SDE invention document from `update-data`: per inventable blueprint its data cores per attempt, product and base chance, and the eight generic decryptors' modifiers. The one `ledger` costs invention with | Non-secret |
| `ledger.sqlite3` (+ `-wal`, `-shm`) | data | The accounting ledger: industry jobs, wallet journal and transactions, market orders, blueprint sightings, reference and opening prices, a sync log, per-sync figure snapshots, the operation's notes and to-dos, planetary holdings polls, and the tables of any other plugin that keeps some. WAL mode, so reports read while a sync writes | **Private business records** (your trades and balances, no credentials). Not regenerable past ESI's 30-day wallet window — back it up like a document |
| Lock sentinels: `tokens.lock`, `config.lock`, `sp-history.lock` (config), `names.lock`, `types.lock`, `quotes.lock` (cache), `watch-state.lock` (state), `update.lock` (data) | beside the file they guard | Zero-length advisory locks, never read or written — `fcntl.flock` on POSIX, a byte-range lock on Windows | inert |

Every durable write goes through one helper: a **unique temporary** file in the destination directory
(`O_EXCL`, opened binary wherever that flag exists so a JSONL file cannot quietly gain CRLF; `0600` up
front for secret payloads) followed by an atomic rename — a crash leaves either the old file or the
new one, never a truncated mix, and two writers can never stomp on each other's temporary. Windows
renames atomically too, but refuses while another process still has the destination open, so the
rename is retried through that window (8 attempts across roughly 1.05 s) rather than losing the write.
Files updated by read-modify-write (token store, config, SP history, name cache, watch state) hold
their advisory lock across the whole read+write, and the OS releases it if the holder dies. Readers
resolve paths with `create=False`, so inspecting state never lays out a directory. An older
single-character `tokens.json` layout is migrated automatically on first load.

Security posture worth knowing:

- Access tokens are sent as `Authorization: Bearer` only to `esi.evetech.net`. Authorization codes,
  refresh tokens and confidential-client credentials are sent only to `login.eveonline.com`; both
  services are contacted over HTTPS with 30-second timeouts.
- The JWT returned by SSO is decoded **without signature verification** — this is a local desktop tool
  reading back its own freshly minted token. Nothing security-critical is decided from those claims.
- ESI responses are cached in-process only, keyed by token+path, with TTLs taken solely from the
  server's `Expires`/`Cache-Control` headers plus ETag revalidation. Nothing sensitive is written to
  disk from ESI apart from public id→name mappings.
- The tool requests read-only scopes; there is no write endpoint in the codebase.
- Backup/erase: the whole footprint is those four directories — config, state, cache, data — wherever
  your platform puts them (on Windows all four sit under `%LOCALAPPDATA%\new-eden-works`).
  `neworks logout` removes tokens locally (all of them, or one with
  `--char`).

Network politeness built in: pinned `X-Compatibility-Date` (`2026-08-18`), a configurable User-Agent,
retries with backoff on 420/429/502/503 honouring `Retry-After`, and automatic pause when ESI's
`X-ESI-Error-Limit-*` headers show the error window filling up.

## `doctor` — installation diagnostics

```bash
neworks doctor                 # offline checks
neworks doctor --json          # same report, machine-readable
neworks doctor --network       # also probe EVE SSO discovery and public ESI endpoints
neworks doctor --timeout 5     # per-request timeout for the probes (default 10 s)
```

Read-only by construction: `doctor` never writes — no token refresh, no endpoint caching, no
migration, no directory creation — so it reports on your install exactly as the next real command
will find it. Offline checks cover the package, the Python version and the OS the report was produced
on (`versions.platform` — a pasted report may be read on a different machine than wrote it), the four
data directories and their permissions (including the state directory the watchers use), config and
token-store readability, per-character login state (token time left, auto-refresh, granted consent —
`orders` and `corp-orders` included), SDE document freshness and where each document resolves from — all
seven of them, `market_types.json` last; as with `blueprint_materials.json`, an absent one is a warning
naming `update-data` rather than a blocker, since it costs one command's subject and not the tool — the
registered callback URLs, SP-history age, and what the watchers have accumulated:

- `watch.state` — watched characters, order owners (split into characters and corporations), known
  open orders, and how long ago any of them was last polled. A state that only holds training data is
  reported as healthy with a hint naming the poll that would start tracking orders; a corrupt file is
  a warning that says to move it aside, because the commands treat it as empty and keep working — what
  is lost is the baseline, so the next watch could re-announce whatever was in flight.
- `watch.events` — how many events are recorded, split between training and order kinds, per-kind
  counts, unreadable lines, and the age of the newest one.

Checks that can only be answered from mode bits — `path.config`, `path.cache`, `path.data`,
`path.state`, `config.file`, `permissions.tokens` — report `skip` on Windows with the reason (privacy
comes from the ACL inherited from the user profile, and every Windows `stat()` claims mode 666), not a
phantom warning with an unrunnable fix. Skips are counted in the summary line as `N skipped`, never as
problems, and they never change the exit code; on POSIX the same checks stay `ok`/`warn` exactly as
before. See [Platform support](install.md#platform-support).

`--network` adds strictly opt-in, unauthenticated, bounded probes of the public SSO discovery document
and three public ESI documents — `/status`, `/meta/compatibility-dates`, and one real market order book
(Jita, `Tritanium`) — that classify *why* a request failed (DNS, timeout, TLS, refused, HTTP status)
instead of reporting "network error". The book probe is the only part that reads rate-limit data, since
`/markets/{region}/orders` is the one endpoint ESI budgets separately: it reports the book's age from
its own `Last-Modified` and how much of the 15-minute budget is left —
`public market data reachable: 160 order row(s), generated 3m 57s ago; rate-limit budget 11994 of
12000 left` — warns when the book is older than a day (that is upstream, not this tool), and reports a
420/429 with the `Retry-After` ESI asks for rather than hammering it. It runs only when `/status`
answered like ESI: one outage is reported once, so a captive portal or an ESI 503 leaves the book probe
as `skip`, not a second failure. `--timeout SECONDS` sets the per-request probe timeout.

What the report contains is deliberate. Credentials never appear: character diagnostics come from a
field whitelist, and every string in the report — text or JSON — passes a redactor seeded with the
credential values found on disk, so an access token, refresh token or client secret cannot escape
even inside an exception message (it prints as `[redacted]`). Paths are shown relative to your home
directory — `~/.config/new-eden-works/tokens.json`, not `/home/you/.config/new-eden-works/tokens.json`, and on
Windows `~\AppData\Local\new-eden-works\config\tokens.json` rather than a profile path carrying your username —
because the expanded form would leak that username into whatever you paste the report into; a
location you configured explicitly outside your home is printed exactly as it is, since naming it is
what the check is for. A path inside a hint — advice meant to be pasted into a shell — is quoted in the
form that running platform's own shell expands, so the line runs as printed: POSIX gets
`chmod 755 "$HOME/.local/state/new-eden-works"` (a tilde inside single quotes is literal, and no directory
of that name exists), Windows gets the profile variable in its place —
`"%USERPROFILE%\AppData\Local\new-eden-works\state"` for that same directory, and
`move "<path>" "<path>.bak"` to set a damaged file aside — since cmd knows neither `~` nor `$HOME`.
What the report does keep is what the diagnostics are about: character names and ids, versions, file
modes where they mean something, counts, URLs and statuses.

Exit code is 1 only when something blocks the tool (unreadable/corrupt token store, an expired
login that cannot refresh, a service unreachable with no cached fallback); everything worth
knowing but survivable is a warning, and warnings exit 0.
