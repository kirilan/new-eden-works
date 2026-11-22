# EVE application and login

## EVE developer application

Authentication uses EVE SSO with your own application registration, so you need one before the
first login:

1. Open <https://developers.eveonline.com/applications> and create an application.
2. Set the **Callback URL** to exactly:

   ```text
   http://localhost:8635/callback
   ```

   Scheme, host, port and path must all match. `localhost` (not `127.0.0.1`), no trailing slash.
3. Tick the scopes you want to be able to use. The tool always requests the core three and adds
   only the optional ones you ask for:

   | Group | Scopes |
   |---|---|
   | Core (always requested) | `publicData`, `esi-skills.read_skills.v1`, `esi-skills.read_skillqueue.v1` |
   | `standings` | `esi-characters.read_standings.v1` |
   | `jobs` | `esi-industry.read_character_jobs.v1`, `esi-industry.read_corporation_jobs.v1` |
   | `assets` | `esi-assets.read_assets.v1`, `esi-assets.read_corporation_assets.v1` |
   | `location` | `esi-location.read_location.v1` |
   | `clones` | `esi-clones.read_clones.v1`, `esi-clones.read_implants.v1` |
   | `orders` | `esi-markets.read_character_orders.v1` |
   | `corp-orders` | `esi-markets.read_corporation_orders.v1`, `esi-characters.read_corporation_roles.v1` |
   | `structures` | `esi-universe.read_structures.v1` |
   | `structure-search` | `esi-search.search_structures.v1`, `esi-universe.read_structures.v1` |
   | `structure-markets` | `esi-markets.structure_markets.v1`, `esi-universe.read_structures.v1` (a structure's market also needs docking access there) |
   | `planets` | `esi-planets.manage_planets.v1` (ESI exposes only GET for the colony routes) |
   | `blueprints` | `esi-characters.read_blueprints.v1`, `esi-corporations.read_blueprints.v1`, `esi-characters.read_corporation_roles.v1` |
   | `divisions` | `esi-corporations.read_divisions.v1` |
   | `wallet` | `esi-wallet.read_character_wallet.v1`, `esi-wallet.read_corporation_wallets.v1`, `esi-characters.read_corporation_roles.v1` |

   Requesting a scope the application is not registered for makes SSO refuse the login.
4. Register it as a native/public application (no secret). Confidential registrations work too —
   pass `--client-secret`, and the tool sends it only in the HTTP Basic header, which is what CCP
   expects.

Supply the client ID once, in any of these ways (highest precedence first):

```bash
neworks login --client-id <your-client-id>          # stored in config.json after a successful login
export NEWORKS_CLIENT_ID=<your-client-id>           # environment override, wins over config.json
# PowerShell equivalent of the same override:
#   $env:NEWORKS_CLIENT_ID = "<your-client-id>"
# or edit config.json where your platform keeps it — ~/.config/new-eden-works/ on POSIX,
# %LOCALAPPDATA%\new-eden-works\config\ on Windows (see [where data lives](data-and-security.md)):
#   {"client_id": "...", "user_agent": "..."}
```

`config.json` also accepts `user_agent`. The default User-Agent says `contact unset`; ESI etiquette
is to publish a contact address, so set it once you use the tool regularly.

## Login

### Local machine (browser opens automatically)

```bash
neworks login
```

The tool prints the authorization URL, tries to open your browser, and listens on loopback for the
redirect. With no `--port` it binds the first free port of 8635-8637; since only
`http://localhost:8635/callback` is registered in step 2 above, free **8635** before logging in, or
pick a fixed port you have also registered:

```bash
neworks login --port 8640                    # per run (register http://localhost:8640/callback)
export NEWORKS_SSO_PORT=8640                    # same thing, persistent for this shell
```

The listener gives up after 5 minutes.

### Remote host (SSH) — `--manual`

On a remote host there is no browser and nothing listening on your workstation's localhost, so use
manual mode:

```bash
neworks login --manual
```

1. The tool prints the authorization URL and never opens a local listener at all.
2. Open that URL in the browser on your **workstation**, log in, approve consent.
3. EVE redirects to `http://localhost:8635/callback?code=...&state=...`. **The browser page failing
   to load ("can't reach this page" / connection refused) is expected.** The authorization code is
   already in the address bar; nothing needs to render.
4. Copy the **complete URL** from the address bar and paste it at the `Paste callback URL:` prompt.
   It must start with `http://localhost:8635/callback?` (if you passed `--port`, that port instead).
5. The tool verifies `state`, exchanges `code` for tokens over HTTPS, and stores them.

Security notes for manual login:

- The pasted URL contains a live authorization code plus the CSRF `state`. Treat it like a password:
  paste it only into this terminal prompt. Do not post it in chat, do not email it, do not commit
  it, do not paste it into a bug report or screenshot.
- Codes are single-use and short-lived; if you fumble the paste, re-run `login --manual` and start
  over rather than reusing an old URL.
- If you accidentally exposed a code before it was redeemed, just abandon that login attempt. If a
  *logged-in* machine may be compromised, run `neworks logout` there and remove the application's
  authorization from your EVE account (that server-side revocation is outside this tool; `logout`
  only deletes local token copies).

### Adding characters, extra consent, logging out

```bash
neworks login                          # another character: pick it in the browser
neworks login --scopes standings,jobs  # add consents for ONE character
neworks login --scopes all             # every optional consent, one character
neworks login --scopes all --repeat 4  # four characters in one command, one consent screen each
neworks chars                          # who is stored, token time left, auto-refresh
neworks logout --char Somename         # drop one character; without --char: all of them
```

`login` itself takes no `--char`: which character gets stored is decided by the EVE account you sign
into in the browser, so run one `login` per character — or `--repeat N` to chain N of them in one
command. EVE still issues one token per character, so that is N consent screens, but the browser keeps
the SSO session between them: characters on the same account cost a pick, not a password.

- Consent is **per character**. Refresh tokens inherit the scopes they were minted with, so adding a
  scope means re-running `login` and selecting that specific character in the browser. The tool
  never re-authenticates anyone implicitly and never bulk-grants.
- Valid `--scopes` values: `attributes`, `standings`, `jobs`, `assets`, `location`, `clones`,
  `orders`, `corp-orders`, `structures`, `structure-search`, `structure-markets`, `planets`, `blueprints`, `divisions`, `wallet`, `all`. An unknown name is a hard error listing the choices.
- **Attributes need no scope of their own.** `attributes` (and exact `plan` costs) use
  `esi-skills.read_skills.v1`, which every login already requests; `login --attributes` is accepted
  for clarity and asks for nothing beyond the core skills consent.
- **Structure names are optional on top of `assets`.** `inventory` reads your holdings with the assets
  consent alone. Naming a player-owned citadel or engineering site is `/universe/structures`' job, and
  that needs `esi-universe.read_structures.v1`: without it those cells read `structure <id>` and the run
  prints the fix once (`neworks login --scopes structures`). A token that provably lacks the scope is
  never sent probing at all — every refusal costs ESI error-window budget that throttles the rest of the
  run.
- **Colonies are read-only, and need their own consent.** `colonies` and the colony half of `skills --watch`
  use `esi-planets.manage_planets.v1`. The name is misleading — ESI's own OpenAPI description (checked
  2026-09-13) implements **GET only** on both colony routes, so this scope cannot write anything to the game.
  Without it every colony command prints `Ada Vane: no planets consent - run: neworks login --scopes planets`
  and exits 0, because a token minted before the scope existed is the normal state, not a failure.
- **`blueprints` and `wallet` read what assets cannot.** Assets name a blueprint but never its runs,
  ME or TE; `blueprints` grants the character and corporation blueprint endpoints. `wallet` grants
  character and corporation wallet balances. Both bundle the roles scope, because the corporation
  halves need Director (blueprints) or Accountant / Junior Accountant (wallets) in-game.
- **Corporation division names are optional.** `blueprints --corp` and `inventory --corp` read
  `/corporations/{corporation_id}/divisions` with `--scopes divisions`. ESI requires the Director
  role. Without it, the commands keep working with `division 1` … `division 7` and print one re-login
  hint; non-hangar flags such as `CorpDeliveries` remain unchanged.
- **Public prices need no consent at all.** `build-cost` and `pi chain` read the public order books,
  so they work on a fresh install with nothing configured. Only *your own* orders are private: the
  ledger and the watch read them with the `orders` consent, and corporation orders additionally need
  `corp-orders` plus the in-game Accountant or Trader role.

### Which commands need which consent

| Command | Consent beyond the core login (`--scopes ...`) |
|---|---|
| `skills`, `summary`, `attributes`, `plan`, `extract` | none (`plan` needs the skill catalog from `update-data`) |
| `build-cost`, `pi`, `system`, `types`, `update-data`, `doctor` | none: public ESI and local game data |
| `chars`, `events` | none, and no network |
| `standings` | `standings` |
| `travel`, `implants` | `location` and/or `clones` |
| `jobs` | `jobs` |
| `blueprints` | `blueprints`; `--idle` also needs `jobs`, and `divisions` names corporation hangars |
| `can-build` | `blueprints,assets,jobs`; `divisions` names corporation hangars |
| `industry status` | `jobs,blueprints,assets,orders` (`corp-orders` for `--corp`); `divisions` names hangars. A missing consent leaves only its section unavailable |
| `inventory` | `assets`; `structures` names player structures, `divisions` corporation hangars, and `jobs` adds delivery warnings |
| `restock` | `assets` (corporation hangars also need the Director role) |
| `colonies` | `planets` |
| `ledger` | `jobs,wallet,orders,corp-orders,blueprints,assets`, once per member |

Errors print `error: <reason>` on stderr and exit 1; a missing optional consent is *not* an error,
just a hint naming the `login` that fixes it. `doctor` also exits 1 when a check reports a blocking
problem.

## Multi-character behavior

- Every stored character is used unless `--char` selects one. `--char` accepts an exact character id,
  an exact name, or a unique case-insensitive substring; ambiguous or unknown values are errors that
  list what is stored.
- `skills` and `summary` isolate fetch failures per character and continue with the rest. Optional
  exports isolate missing consent per character; corporation job/asset authorization failures become
  warnings. Other ESI failures can still abort an export command.
- Commands that need a single subject (`plan`, `extract`) demand `--char` when several are stored.
- Add characters by running `login` again and choosing a different character in the browser; there is no
  bulk add.
