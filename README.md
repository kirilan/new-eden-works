# New Eden Works

New Eden Works (`neworks`) is a command line toolkit for running an EVE Online operation from your own
machine: characters and skill training, industry jobs and production planning, planetary industry, an
accounting ledger of what the business cost and earned, and a web dashboard. Each area is a plugin you
can switch on or off. An MCP server and a set of agent skills let AI agents (Claude Code, Codex, Hermes
and others) use the same tools.

- Linux and Windows, Python 3.11+, **standard library only**: no runtime dependencies, nothing to
  compile. Windows users can also take a standalone `neworks.exe`. See [Platform support](#platform-support).
- **Read-only against ESI.** It never spends ISK, moves anything, trains skills or changes the game.
- **Multi-character.** One login per character; every command walks all stored characters unless you
  pick one with `--char`.
- Text output by default, `--json` / `--csv` for scripts, `--watch` for a live terminal view.
- **Runs on your machine.** No hosted service: tokens, the ledger and settings stay in your profile.
- **For agents:** `neworks mcp` serves every command as an MCP tool, and `neworks agent install` gives
  Claude Code, Codex or Hermes the agent skills. See [Agents](#agents-mcp-server-and-skills).

```console
$ neworks summary                         # one line per character
$ neworks skills --char Somecharacter     # skills, queue and clone state
$ neworks industry status --corp          # slots, jobs, idle blueprints and stock in one view
$ neworks build-cost Hound                # build it or buy it, priced off the live order books
$ neworks ledger pnl --by week            # what the operation earned
$ neworks dashboard serve                 # the web dashboard on http://localhost:8765/
```

---

## Quick start

These steps take you from nothing to a logged-in character. The sections further down go into detail.

**1. Install.** With Python 3.11+ and [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/):

```bash
pipx install git+https://github.com/kirilan/new-eden-works.git
# or: uv tool install git+https://github.com/kirilan/new-eden-works.git
neworks --version
```

On Windows without Python, download `new-eden-works-<version>-windows-x64.zip` from the
[releases page](https://github.com/kirilan/new-eden-works/releases), extract it somewhere permanent and
run `.\neworks.exe --version` there ([details](#windows-standalone-executable)). To work on the code, see
[Install](#install).

**2. Download the game data** (~100 MB, once, and again after a game patch). Skill plans, build costs,
planetary industry and restock lists read recipes and skills from it:

```bash
neworks update-data
```

**3. Register an EVE application** at <https://developers.eveonline.com/applications>: callback URL
exactly `http://localhost:8635/callback`, and tick every scope in the
[scope table](#eve-developer-application), since step 4 asks for all of them. Copy its **Client ID**.

**4. Log in**, once per character. A browser opens; pick the character and approve:

```bash
neworks login --client-id <your-client-id> --scopes all   # the client ID is remembered afterwards
neworks login --scopes all                                 # the next character
```

On a machine reached over SSH, add `--manual` and paste the URL the browser ends on back into the
terminal; see [Remote host](#remote-host-ssh----manual).

**5. Check the installation**, and give ESI a contact address as CCP asks tools to:

```bash
neworks doctor                    # every check, with the fix for anything that is not ok
```

Then add `"user_agent": "new-eden-works (you@example.com)"` to `config.json`
([where it is](#where-data-lives-and-how-it-is-protected)).

**6. Use it:**

```bash
neworks summary                       # one line per character
neworks skills                        # skills, queue and clone state
neworks jobs --slots                  # industry jobs and free slots per character
neworks colonies --detail             # planetary colonies and when each extractor runs out
neworks ledger sync && neworks ledger pnl   # book wallet and jobs into the ledger, then the profit
neworks plugins                       # what is loaded, and which plugin gives each command
```

`neworks <command> --help` shows any command's options; [What's included](#whats-included) lists them all.

**7. Optional next steps:**

- **Describe your operation** (sites, facilities, hubs, freight, finish windows) so planning commands
  know where you build and sell: `neworks operation template > operation.json`, edit it, and put it in
  the config folder. See [operation](#operation--sites-facilities-structure-markets-and-freight).
- **Run it continuously:** one background service runs the scheduled jobs (Telegram alerts, the
  dashboard): see [service](#service-and-run--every-scheduled-job-in-one-process).
- **Let an agent use it:** `neworks agent install --target claude-code` in your project, then follow
  what it prints. See [Agents](#agents-mcp-server-and-skills).

---

## What's included

The commands come from plugins. `neworks plugins` lists the loaded ones and what each provides;
`config.json`'s `plugins.enabled` picks which load (all of them when absent).

| Plugin | What it covers | Commands |
|---|---|---|
| core | logins, item and system look-ups, inventory, the operation file, diagnostics, and the harness itself: plugins, watch, alerts, the service, MCP and agent skills | `login`, `logout`, `chars`, `events`, `inventory`, `operation`, `system`, `types`, `doctor`, `update-data`, `plugins`, `watch`, `alerts`, `mcp`, `agent`, `migrate`, `service`, `run` |
| `characters` | skills, training queue, clone state, plans, extraction, attributes, standings, location and implants | `skills` (the default), `summary`, `attributes`, `standings`, `travel`, `implants`, `plan`, `extract` |
| `industry` | industry jobs, blueprints, build costs, restock lists, saved production rounds and the job board | `jobs`, `industry`, `can-build`, `blueprints`, `build-cost`, `restock`, `production`, `job-board` |
| `planetary` | planetary industry recipes, colony fits and your live colonies | `pi`, `colonies` |
| `ledger` | the accounting ledger: costs, profit, stock value, notes and progress | `ledger` |
| `dashboard` | a local web page made of every loaded plugin's panels | `dashboard` |

Market commands (order books, your own orders, price signals and sell plans) are not part of this
release. Other plugins install as their own Python distributions and register through the
`newedenworks.plugins` entry point group; see [Writing a plugin](#writing-a-plugin).

| Command | Purpose | Extra consent needed |
|---|---|---|
| `skills` (default) | Clone state + evidence, totals, training queue, trained-skill table | no (core login) |
| `summary` | One row per character: clone state, total SP, queue length, current item time left, grand total | no |
| `attributes` | Effective attributes (fitted implant bonuses included), remaps available/last remap | no — covered by the standard skills consent |
| `build-cost` | What manufacturing one item costs right now: per-material buy-or-build table, the install fee with its arithmetic shown, and the same unit bought instead as a verdict; `--brief` keeps only the decision output; `--runs`, `--me`/`--te`/`--component-me`, `--build`/`--buy`, `--hub`/`--region`/`--system` | no — public ESI; recipes come from local SDE data (`update-data`) |
| `pi` | Planetary industry off the local SDE: `chain` — one product's whole recipe tree with per-step price, value added per facility-hour and an optional customs column; `fit` — a colony layout against its command-centre budget and how many extractor heads still fit; `planet-type` — what one planet yields and everything it can refine with no imports | no — `chain` reads public order books; `fit` and `planet-type` need no network at all, and recipes, fitting costs and customs values come from local SDE data (`update-data`) |
| `colonies` | Live planetary industry per character: every colony with its planet type, command-centre level, pin count and how old ESI's view of it is; `--detail` adds each extractor with what it produces and when the programmed run ends, plus every facility running a schematic | `--scopes planets` |
| `system` | One row per solar system: true security status and the figure the client shows, highsec/lowsec/nullsec, region, planet types with counts, and jumps to a hub with `--route HUB`; several systems at once | no — public ESI; the planet breakdown comes from local SDE data (`update-data`) |
| `types` | Type id, exact name, group, category and packaged m3 for item names or ids; `-` reads a pasted list (`name<TAB>qty`) and adds an m3 total | no — public ESI; the type catalogue is cached on disk |
| `chars` | Stored characters, access-token time left, auto-refresh availability | offline (no network) |
| `events` | Recorded watch alerts: training finished / queue emptied / your orders filled, expired or cancelled / an extraction finished | offline (no network) |
| `standings` | Agent / NPC corp / faction standings | `--scopes standings` |
| `jobs` | Personal or `--corp` industry jobs, with installer and duration details; `--group` collapses identical lines, `--slots` shows each character's manufacturing, science and reaction budget, and `--json` exposes cache timestamps | `--scopes jobs` |
| `blueprints` | Blueprint originals and copies with runs, ME/TE, location and count; `--idle` removes copies tied to current jobs, filters can select kind, type or corporation division, and corporation output identifies stale snapshots | `--scopes blueprints`; `--idle` and delivery checks also need `jobs`; `divisions` names corporation hangars |
| `can-build` | Idle blueprint jobs allowed by stock in each blueprint's own division/location, with the limiting material, cross-hangar warning, and per-builder skill check | `--scopes blueprints,assets,jobs`; `divisions` names corporation hangars |
| `industry status` | Compact, diffable snapshot of per-character slots, grouped jobs, idle blueprints with local can-build counts, division material stock, open orders and remote corporation deliveries; `--materials FILE` adds named materials, `--json` retains ids and ESI cache timestamps | `--scopes jobs,blueprints,assets,orders` (or `corp-orders` for `--corp`); `divisions` names hangars; core skills for slot maxima. Missing consents leave only their sections unavailable |
| `inventory` | Assets named, placed and valued: per-location summary, `--by category` or corporation `--by division`, one row per item with `--items`, division filters, cache freshness, a real standing bid with `--value-at jita`, or full `--csv` | `--scopes assets`; plus `structures` to name player structures, `divisions` to name corporation hangars, and `jobs` for delivery warnings |
| `travel` | Current location, home, jump clones with their implants | `--scopes location` and/or `clones` |
| `implants` | Implants fitted in the active clone | `--scopes clones` |
| `plan` | Ordered, priced training path to target levels incl. auto-added prerequisites | no — needs the SDE skill catalog (`update-data`) |
| `extract` | Skill Extractor count, injector yield, re-training cost | no |
| `update-data` | Refresh alpha caps, the full skill catalog and every blueprint's material list from the official SDE (~100 MB download) | no |
| `doctor` | Diagnose install, stored logins and data freshness; `--network` probes SSO/ESI | no |

Run `neworks <command> --help` for the full option list of any command. Errors print
`error: <reason>` on stderr and exit 1; a missing optional consent is *not* an error — see the
consent notes in the [login section](#adding-characters-extra-consent-logging-out). `doctor` is
the one handler with its own exit semantics: it also exits 1 when a check reports a blocking
problem.

---

## Install

Python 3.11 or newer, standard library only — no runtime dependencies and nothing to compile. Linux
and Windows are both supported; [Platform support](#platform-support) says exactly what differs.

On Linux, install it as the Python package it is (below). On Windows you can do the same, or skip
Python entirely with the [standalone executable](#windows-standalone-executable).

### With uv

[uv](https://docs.astral.sh/uv/) is the shortest path, and it needs no pre-existing virtualenv.

Try it with nothing installed:

```bash
git clone https://github.com/kirilan/new-eden-works.git
cd new-eden-works
uv run neworks --version                                  # -> neworks 0.2.0; no install step
uv run neworks system Jita                                # public data, before any login
uv run --with setuptools python -m unittest discover -s tests -t . -q   # the whole suite
```

`--with setuptools` is not decoration: the packaging tier builds a real wheel and sdist to inspect,
so without a build backend its two build-and-install classes skip their tests.

Keep a project environment in sync with the lockfile — this is the install and update path:

```bash
uv sync                            # creates .venv and installs the project (and updates it later)
uv sync --extra release            # …plus the build/twine tooling RELEASE.md uses
uv run neworks chars            # runs against that environment
```

Install it as a tool on your `PATH` for daily use:

```bash
uv tool install .                                                # from this checkout
uv tool install git+https://github.com/kirilan/new-eden-works.git    # …or straight from GitHub
uv tool list                                                         # -> new-eden-works v0.2.0
uv tool uninstall new-eden-works                                     # …and take it off again
```

The console scripts `neworks` and `new-eden-works` land in uv's own tool directory (`~/.local/bin`
on Linux); if the shell cannot find `neworks` afterwards, `uv tool update-shell` puts that directory on `PATH`.

Development means an editable install inside a uv-managed virtualenv:

```bash
uv venv                         # -> .venv (uv found CPython 3.14.4 here)
uv pip install -e .
```

**Read that one before running it.** `uv pip install` installs into the **active** `VIRTUAL_ENV` when
one is set — not necessarily the `.venv` beside you — so an editable install can quietly land in
whichever environment your shell happens to have activated, leaving this checkout uninstalled. Pin the
target explicitly, or work from a shell with nothing active:

```bash
uv pip install -e . --python .venv    # always this directory's .venv, whatever else is active
echo "$VIRTUAL_ENV"                   # empty output = nothing active (PowerShell: echo $env:VIRTUAL_ENV)
```

### Without uv

A stdlib virtualenv needs no extra tooling. POSIX shells:

```bash
git clone https://github.com/kirilan/new-eden-works.git
cd new-eden-works
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/neworks --version
```

PowerShell — the interpreter is `python`, and console scripts are `.exe` files under `Scripts`:

```powershell
git clone https://github.com/kirilan/new-eden-works.git
cd new-eden-works
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\neworks.exe --version
```

### Windows standalone executable

Each release carries `new-eden-works-<version>-windows-x64.zip` on the
[releases page](https://github.com/kirilan/new-eden-works/releases), next to a `.sha256` file with its checksum.
Inside are `neworks.exe`, `LICENSE` and a short `README.txt`. The executable is the same code as the
package, frozen with [PyInstaller](https://pyinstaller.org) together with its own Python and the bundled
SDE snapshot, so nothing else needs installing: every command, flag and output format in this README
applies unchanged.

```powershell
# after extracting the zip to %LOCALAPPDATA%\Programs\new-eden-works
$dir = "$env:LOCALAPPDATA\Programs\new-eden-works"
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$dir", "User")
# open a new terminal, then:
neworks --version
```

What to expect:

- **It is a console program.** Run it from PowerShell or Windows Terminal; double-clicking it in
  Explorer only shows a note saying so and waits for Enter.
- **Your data does not live next to it.** Logins, config, caches and `update-data` downloads go to
  `%LOCALAPPDATA%\new-eden-works`, exactly as with the Python package — so upgrading is replacing the
  `.exe`, and switching between the executable and a pip install keeps every login.
- **SmartScreen may warn on first run.** The executable is not code-signed, so Windows can show
  "Windows protected your PC"; *More info → Run anyway*. Compare the zip against its `.sha256` if you
  want to be sure it is the file the release workflow built.
- **Each start unpacks itself** to a temporary folder first, so a command takes a little longer to
  begin than under a Python install — more while an antivirus scans the unpacked files. Irrelevant for
  `--watch` and `dashboard serve`; if you script hundreds of calls, the Python package is the better fit.

There is no Linux build of this kind on purpose: there the package installs with tools every
distribution already has.

### No install at all

The package is plain stdlib Python, so from the checkout you can skip installation entirely and run
the module — the same `main()` the console script calls:

```bash
python -m newedenworks skills --json
```

Everything below assumes `neworks` is on `PATH`; substitute `python -m newedenworks` if it is not.
`new-eden-works` is the same command under its full name (scripts, service units and MCP configs use it).

### Upgrading from eve-skills

New Eden Works was called eve-skills up to 0.1.0. The commands are the same, under the name `neworks`.
Its folders were named `eve-skills`; they keep working, untouched, until you run
[`neworks migrate`](#moving-from-the-eve-skills-folders-migrate), which copies them to the new names. The
`EVE_SKILLS_*` environment variables are still read; their new names are `NEWORKS_*`.

To build a wheel or source distribution instead of installing from the checkout — and for the
version-bump, artifact-inspection, clean-install and tag procedure — see
[RELEASE.md](RELEASE.md). Everything shipped here is GPL-3.0-only; the full text is in
[LICENSE](LICENSE) and travels inside both artifacts.

---

## Platform support

Linux and Windows are both supported: Python 3.11+, standard library only, no compiled dependency and
no platform-specific package. Commands, flags, output formats and exit codes are identical. Four things
really do differ, and each is handled rather than papered over — with a last note on how much of any of
it has actually been run.

**Locking.** A long-running `--watch` and a manual command can touch the same files, so every
read-modify-write holds an advisory lock — on POSIX with `fcntl.flock` (whole-file, blocking, dropped
by the kernel when the descriptor closes or the holder dies), on Windows with `msvcrt.locking` over
the first byte of that same lock file (`LK_NBLCK` is refused rather than queued, so it is polled every
20 ms, backing off to 250 ms until granted, then released with `LK_UNLCK`; the OS drops it when the
handle closes). The durability guarantees are the same on both: one writer at a time, nothing wedged
by a crashed watcher, and every durable write still a unique temporary plus an atomic rename — a crash
leaves the old file or the new one, never a mix. Two Windows details follow from its own contracts: a
rename fails outright while another process still has the destination open, so `os.replace` is retried
8 times across roughly 1.05 s before the original error propagates; and temporaries are opened
`O_BINARY`, because a text-mode handle would turn every `\n` written to `events.jsonl` into `\r\n`, and
its readers count lines. The lock is held on a zero-length sentinel file that nobody reads or writes —
precisely so the one real difference between the mechanisms (an `msvcrt` byte-range lock denies other
processes access to the locked region, while `flock` stays advisory even for its own file) can never
reach your data.

**File permissions.** On POSIX secret files are created `0600`, and `doctor` checks directories for a
group/other write bit. Windows has no such bits: a new file inherits the ACL of its directory, which
is normally your private user profile — that inheritance is what protects `tokens.json` there, not a
mode. So on Windows `doctor` reports `skip` for exactly the checks it cannot make (`path.config`,
`path.cache`, `path.data`, `path.state`, `config.file`, `permissions.tokens`) and says why, instead of
warning about the mode 666 every Windows `stat()` reports and offering a `chmod` that does not exist. A
skip is never a blocker: skips do not change the exit code, and everything else — including a corrupt
token store — still fails or warns as usual. To look at privacy yourself, use the folder's Security
properties; there is nothing in this tool to configure.

**Desktop notifications.** `--notify` shells out to `notify-send`, which no stock Windows installation
ships: treat it as a Linux feature. Everything else happens everywhere — an alert rings the terminal
bell, is printed, and is recorded in the event history either way. And rather than going quiet after a
flag that promised a ping, the watch says once per run:
`warning: desktop notifications need notify-send; events are still printed and recorded`.

**Watch redraw.** The dashboard clears the screen between frames when stdout is a TTY. A Windows
console ignores the escape sequence unless virtual-terminal processing is switched on, so the watcher
turns it on once per run; a console that refuses gets a plain 72-character rule line instead of escape
codes, with the timestamped header still separating the frames.

**How much of this has been run.** Every Windows branch — the `msvcrt` backend, profile-folder path
resolution, doctor's skips and its cmd-shaped hints, the virtual-terminal fallback — is covered by tests
that *force* the platform (a fake `msvcrt`, an injected platform judgement), and the whole suite also
runs on a real `windows-latest` runner in CI, where the [standalone executable](#windows-standalone-executable)
is built and smoke-tested too. What no runner covers is a desktop: double-clicking the `.exe`,
SmartScreen, roaming profiles, an antivirus holding files open. See
[Development](#development) for how it is tested.

---

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
export EVE_SKILLS_CLIENT_ID=<your-client-id>           # environment override, wins over config.json
# PowerShell equivalent of the same override:
#   $env:EVE_SKILLS_CLIENT_ID = "<your-client-id>"
# or edit config.json where your platform keeps it — ~/.config/new-eden-works/ on POSIX,
# %LOCALAPPDATA%\new-eden-works\config\ on Windows (see [where data lives](#where-data-lives-and-how-it-is-protected)):
#   {"client_id": "...", "user_agent": "..."}
```

`config.json` also accepts `user_agent`. The default User-Agent says `contact unset`; ESI etiquette
is to publish a contact address, so set it once you use the tool regularly.

---

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
export EVE_SKILLS_SSO_PORT=8640                 # same thing, persistent for this shell
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

---

## Commands

### `skills` — the main view

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

### `summary` — fleet-wide one-liner

```bash
neworks summary
```

One row per fetched character (clone state + confidence, total SP, queue items, time left on the
currently training item) plus a `TOTAL` row. Characters that fail to fetch are reported as
`warning: skipped ...` on stderr and do not abort the command.

### `attributes`

```bash
neworks attributes --char Somecharacter
```

PER/INT/MEM/CHR/WIL and remaps available with the last remap date. These are **effective** values —
fitted implant bonuses are already inside them (verified against live training, 2026-09-13), which
is worth knowing before spending a remap on them. No extra consent is needed; a character whose
stored consent somehow lacks the skills scope gets a hint line instead of data.

### `standings`, `jobs`, `travel`, `implants`

```bash
neworks standings --csv > standings.csv
neworks jobs                       # personal jobs
neworks jobs --corp --completed    # corp jobs incl. finished/cancelled
neworks jobs --corp --times --finish-window 08-10,20-22 --tz Europe/Helsinki --start 09:30
                                      # measured hours per run, and run counts ending in those windows
neworks jobs --times --site null   # hours per run where an operation-file site does each activity
neworks jobs --since 08:00 --site null   # what was installed since 08:00: "did I miss anything"
neworks travel                     # current location, home, jump clones + their implants
neworks implants                   # implants in the active clone (one row per fitted instance)
```

All four accept `--char` and `--csv`. In CSV mode any consent hint is written to **stderr** so stdout
stays machine-readable. Corporation variants (`jobs --corp`, `inventory --corp`) additionally need
the matching director / Account-Manager role on that character; without it ESI refuses and the tool
prints a per-character warning rather than failing the whole command.

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

### `can-build` — jobs allowed by stock and skills

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


### `industry status` — one operational snapshot

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

### `inventory` — what you own, where it is, what it is worth

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

### `restock` — what the next builds still need, and what to buy for them

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

**Price check.** Every material the trip buys is then judged by the `prices` dip rule from the buyer's
side: the hub's lowest ask against the median daily average of the hub's region over 30 days (ESI history,
read live; `--no-price-check` skips it). A **dip** (two spreads under normal, and not low for the whole
last week) adds extra units: the trip's need times 1.5 under 5% below normal, 2 from 5%, 3 from 10% and 4
from 20% (deeper dips have paid more: over a year of Forge history for our 22 main inputs a unit bought on
the first dip day cost a median 4% / 7% / 9% / 24% less in those bands than on recovery or two weeks
later, measured on daily averages, so the saving at the ask is smaller). The extra never takes the
material past 14 days of use counting what we already own (`--days`, default 3, says how many days the
lines cover), and comes only from asks still under the dip line once the trip's own buy has taken the
cheapest ones. A **high** input (two spreads over) is flagged and its `--extra` buffer is left for a
cheaper day; **falling** buys nothing ahead. The text adds a price-check table and, when anything
changes, a second Multibuy with the check applied; `--json` has a `price_check` object. Dip extras are
not fitted to `--cargo`; a note says when they do not fit what is left.

### `operation` — sites, facilities, structure markets and freight

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
in `<config dir>/playbook/` (or `$EVE_SKILLS_PLAYBOOK`), where they cannot be committed by accident.
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

### `production` — continue a round, supply it and replenish its sales

`production round save/check` persists the intended jobs and compares them with observed installs.
The remaining jobs share one bulk blueprint/material pool, passed from installer to installer.
`production supply` forecasts shortages and purchase deadlines; `production replenish` recommends
listing or hauling existing stock before building more. `production scan` compares product economics
with manufacturing and invention capacity. `production facility` previews and records dated facility
settings for historical ledger costing.

See [Production workflow](docs/production.md) for examples, the round JSON format, freshness rules and
costing assumptions. Run `ledger sync` after upgrading to create the new tables; reports do not migrate
a ledger opened read-only. Plans and handoff confirmations remain private in the ledger.

### `ledger` — what the business cost, earned and holds

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

### `dashboard` — profitability, industry and planetary industry in a browser

```bash
neworks dashboard serve                         # http://<this machine>:8765/, rebuilt every 60 minutes
neworks dashboard serve --host 127.0.0.1 --port 9000 --since 2026-09-19
neworks dashboard build --out dashboard.json    # the same data once, as JSON
```

`serve` runs a small web server (standard library only) and one refresh thread. Every `--refresh` minutes
(default 60) it runs `ledger sync` when the last sync is that old (`--no-sync` leaves syncing to someone
else), replays the ledger, and runs the panels' live reports: `industry status --corp` for slots and jobs,
`colonies --detail` for extractor timers (`--no-live` skips them). A plugin's quick panel refreshes more
often, every `--orders-refresh` minutes (default 20; 0 turns it off). The page polls for new data every 5
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
up*, 7-14 is *slow*. Beside it, *market days* (on hand / units Jita's region trades per day, from the
`prices` history) tells a line the market could absorb but we have not listed or priced right from one the
market is too thin for. *Are we running out of an input?* Per item jobs consume (materials, data cores,
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
after a crash; turn it on in config.json as [service](#service-and-run--every-scheduled-job-in-one-process)
shows. A refresh that fails (ESI down, a report erroring) is logged and the page keeps the last good data,
showing the error. A service running from a source checkout runs whichever branch is checked out there and
picks up new code (and a new page) only when restarted. The ledger refuses to be opened by code older than
its schema ("written by a newer version"), so switching such a checkout to an older branch stops the jobs
until it is switched back.

### `alerts` — industry job alerts in Telegram

```bash
neworks alerts setup --token-file token.txt   # token from @BotFather; message the bot once first
neworks alerts test "hello"                   # one line to the chat
neworks alerts run --dry-run                  # print what would be sent; send nothing, save nothing
neworks alerts run                            # what the service's alerts job runs every 20 minutes
```

`run` asks each plugin's **alert source** for its news: industry's `jobs` reads every character's
industry jobs, and other plugins can add sources of their own (a market plugin's orders, say). The core
owns the channel, the `notify` settings, quiet hours and the one reminder clock the sources share
(`notify.json` in the state root; each source keeps its own state beside it). One Telegram message then
lists what is new since the last run:

- **Jobs ending**, once per industry job, when it has at most `job_notice_minutes` (default 20) left or
  has already finished. Until it is delivered, the job is listed again in the reminder, at most every
  `remind_hours` and never in quiet hours. A delivered job leaves the character's job list and is
  dropped without a message; a paused job is not news. Jobs come from each character's own job list,
  which includes the jobs it installed for its corporation.
- **Window summary**, once per finish window, on the first run inside it: the jobs ready to deliver per
  character, the free manufacturing and science slots per character, and the jobs still ending inside
  the window. It is sent only when something is ready or a slot is free, and the jobs it lists count as
  announced. The windows are `industry.jobs.windows` in config.json, else the operation file's
  `planning.finish_windows`; with neither there are no summaries. Slots are read only on that run.
- **Batching:** jobs finish one by one over an hour or two, so a job due for its "jobs ending" line is
  held back while another job of the same site (the operation file's sites; everyone is one site
  without one) ends within `industry.jobs.batch_minutes` (default 45), unless the oldest held job has waited
  `industry.jobs.max_wait_minutes` (default 90). Then the whole site goes out in one message. In quiet hours no
  job news is sent at all; the next window summary covers the night. A failed jobs read skips the
  summary and the batching for that run (the per-job lines work as before).

The message is laid out for a phone: a title with the counts, then short lines. Jobs are counted per
facility, character and activity, with the time left; the products are not listed.

```
EVE: 7 jobs ending

⚙ Y-EXMPL
   Builder One: 4 manufacturing · ready
   Builder Two: 2 manufacturing, 1 copying · in 5–12 min
```

Settings live in config.json, the shared ones in the core's `notify` section and each source's in its
plugin's section; `alerts setup` writes a `notify` section with the defaults. For example:

```json
{
  "notify": {"exclude_locations": ["Z-EXCL"], "exclude_characters": [], "remind_hours": 4,
             "quiet_hours": "00:00-08:00", "timezone": "Europe/London"},
  "industry": {"job_alerts": true, "job_notice_minutes": 20,
               "jobs": {"enabled": true, "windows": null, "batch_minutes": 45, "max_wait_minutes": 90}}
}
```

- `notify`: `exclude_characters`, `exclude_locations` (case-insensitive parts of a station name, e.g.
  `"Z-EXCL"`, for sources that report places), `remind_hours` (default 4; 0 = no reminders), `quiet_hours` (default
  `"00:00-08:00"`; messages then arrive silently, and job news waits for the morning) and `timezone`
  (default `UTC`; an IANA name needs a time zone database, which Windows only has with `pip install
  tzdata`, otherwise use a fixed offset such as `+02:00`, which ignores daylight saving).
- `industry`: `job_alerts` (default `true`), `job_notice_minutes` (default 20; the job runs every 20
  minutes, so a shorter notice can miss straight to "done") and `jobs` (default `{"enabled": true,
  "windows": null, "batch_minutes": 45, "max_wait_minutes": 90}`: the window summary and batching;
  `enabled: false` turns both off).
`doctor` reports a value it cannot use, naming its section. Before config.json had these sections the
same keys sat in one flat `alerts.json` in the config directory; it is still read, unchanged, while
config.json has no `notify` section, and `neworks migrate --apply` moves it into the sections (checking
that every setting stays the same) and renames it `alerts.json.migrated`.

The bot token and chat id are in `telegram.json` in the config directory, owner-only. The token is read
from a file or stdin, never from the command line, and no error message contains it. State
(`job-alerts.json` and `notify.json`, state directory) is saved only after Telegram accepted the message,
so a failed send is retried by the next run. The [service](#service-and-run--every-scheduled-job-in-one-process)'s
`alerts` job runs `alerts run` every 20 minutes.

### `build-cost` — what manufacturing an item costs right now

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
best sell (with a market plugin's `trade.listing_price` hook loaded, one tick under the real floor of the
book, ignoring a thin, clearly cheaper sliver and the seller's own orders), the net
per unit after that character's sales tax and the station's broker fee (from the market plugin's
`trade.seller_terms` hook; without one the net and the margin stay empty and the output says why), the margin against the build's
cost per unit, per job and per job-hour, and how many days of the region's 30-day volume the job's
output is. Several products print one table ranked by ISK per job-hour, which is how lines compete for
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
picks the tax, and the surcharge is CCP policy (see [Limitations](#limitations-you-should-know)).

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

### `pi` — planetary industry: recipe trees, colony fits, planet yields

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

### `colonies` — what is actually on your planets right now

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
run (see [adding consent](#adding-characters-extra-consent-logging-out)).

Machine output: `--json` gives one entry per character with each colony's ESI fields plus `age_seconds`, `stale`
and `layout_fetched`, and — where a layout was read — `pins_read`, `extractors` (each with `seconds_to_expiry`,
negative once the run ended) and `facilities`; consent hints, per-colony fetch warnings and naming notices
travel as the `hints`, `warnings` and `notices` arrays. `--csv` follows the flag: one row per colony by default,
one row per extractor with `--detail`, where the colony columns repeat so every row says where that extractor
stands and a colony without one keeps its row with those cells empty. A colony whose pin document ESI refused
keeps its summary row, loses only its own pins, and says why on stderr; the run still exits 0.

### `types` — what an item is and how big it packs

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

### `system` — security, planets and jump distance for candidate systems

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

### `plan` — training plan (one character)

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

### `extract` — Skill Extractor math (one character)

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

### `watch` — live terminal dashboard

```bash
neworks skills --watch          # refresh every 5 minutes (training + your market orders)
neworks skills --watch 1        # every minute (minimum 1)
neworks skills --watch 10 --notify
neworks skills --watch --no-orders   # training only: leave the order books alone
neworks skills --watch --no-colonies   # leave the planets alone too
neworks skills --watch --ledger # also keep the accounting ledger synced (hourly)
neworks watch                   # the same loop as skills --watch, every 5 minutes (--every MIN)
```

The loop polls the plugins' **watch sources**: `skills` (training, from the characters plugin), `orders`
(the core's own order books), `colonies` (planetary) and `ledger` (with `--ledger`). `skills --watch` and
`watch` poll every source the flags leave on. A disabled plugin's source is simply not polled, and `plugins` lists each plugin's
sources.

By default watch draws a compact status table for all stored characters — clone state, queue
length with finished-item count, what is training now and its time left (or `blocked (no
schedule)`), total SP, and fetch freshness. It redraws on the timer (clearing the screen only
when stdout is a TTY — on a Windows console only after virtual-terminal processing has been switched
on, with a plain rule line as the fallback for one that refuses it; see
[Platform support](#platform-support)). `--full` keeps the full `skills` view instead. Ctrl-C stops it
and exits 130.

A character whose fetch fails keeps its last-known row marked `<age> stale`, with the error
printed under the table — healthy data is never dropped because one sibling went quiet; a
character never fetched shows `no data yet`. A cycle where no character data could be fetched at
all prints `warning: no character data this cycle - retrying next poll` and keeps the previous
training state, so a transient ESI outage cannot fake a "finished" event.

Alerts fire **exactly once**: every transition a watcher witnesses — training finished, queue
emptied, an order filled, expired or cancelled — is claimed against persisted state under a lock,
so each event is announced once across polls, restarts and concurrent watchers — never repeated
per poll. Announcements ring the terminal bell; `--notify` additionally calls `notify-send` when that
binary exists, which in practice means Linux — Windows ships nothing by that name, so the watch prints
one explanation per run rather than going quiet, and the bell, the printed line and the recorded event
are unchanged. A missing, hanging or failing notify-send never kills an overnight watch. The history
lives in the state directory (`$XDG_STATE_HOME/new-eden-works`, `%LOCALAPPDATA%\new-eden-works\state` on
Windows) and survives restarts — read it with
[events](#events--recorded-watch-history). There is no mail/push/webhook delivery.

`skills --watch` polls the order books too — the run you already leave open overnight is the one that
should notice a fill — and `--no-orders` opts out. Order state lives beside the training state, keyed
by **owner** (`char:<character_id>`, or `corp:<corporation_id>` for a corporation book), because the
same corporation can be watched through any colleague's token. Two rules follow from that:

- **The first poll of an owner announces nothing.** ESI's ~90-day order history is ingested silently,
  so starting a watch does not shout months of old closures as if they had just happened. They are
  still recorded, and `events` marks them `[history]`.
- **A fill, an expiry or a cancellation happens once, ever.** Ids already settled are remembered, so a
  history row surfacing in a later poll — or a second watcher on the same character — adds neither a
  row nor noise. An order that leaves the live book without a matching history row waits two days for
  ESI's backlog to catch up, then is recorded as `order_closed`; if it reappears meanwhile it was a
  cache flicker and nothing is said. A cycle whose history fetch failed freezes conclusions the same
  way: the open book is refreshed and a disappearance still starts its wait, but nothing is declared
  closed on a cycle that could not check.

`skills --watch` also polls colonies for every character holding the `planets` consent, and `--no-colonies`
opts out of that the same way. An extraction that ends while the watch is open announces
`Ice colony in Ditalren - extraction of Heavy Water finished (15/cycle, 3 heads)` once, as
`extractor_expired`. Two things make that trustworthy without any in-game marker: `expiry_time` is an
absolute instant, so "it just ended" is a witnessed crossing of the clock rather than a data change, and
reprogramming an extractor forces ESI to recalculate — a pin moved back into the future returns to being
watched quietly and its next expiry is a separate event with its own id. The first sight of a colony never
announces, even for pins that ran out weeks ago. Requests stay inside ESI's stated freshness: both routes
publish `x-client-cache-ttl: 600`, so at the default interval a character with four colonies costs at most
five conditional GETs per ten minutes.

### `events` — recorded watch history

```bash
neworks events                      # most recent 50 events, newest first
neworks events --char Somecharacter --limit 200
neworks events --kind order_filled  # repeatable: one kind per flag
neworks events --owner "corp:98356123"      # one owner's order events, by exact key…
neworks events --owner ledger               # …or by part of its name, case-insensitive
neworks events --kind extractor_expired   # extractions that ended while a watch was open
neworks events --json
neworks events --csv > events.csv
```

A read-only view of the alert history the watchers record: it never fetches and never writes. Kinds
are `training_finished`, `queue_empty`, `order_filled`, `order_expired`, `order_cancelled`,
`order_closed` and `extractor_expired`; `--kind` filters to any subset and composes with the other filters.

`--char` accepts a stored name or id; a bare numeric id also finds events for characters already
logged out. Order events belong to an *owner*, not necessarily to a character — a corporation
announcement deliberately has no `character_id`, because it was read through one colleague's token
but is nobody's personal order — so `--char` cannot reach those rows and `--owner` exists for them:
exact owner key (`char:90000001`, `corp:98000001`) or a case-insensitive fragment of the owner name.
Training events have no owner, so an `--owner` filter never returns one.

Unreadable history lines are skipped with a
`warning: skipped N unreadable event history line(s)` on stderr. When nothing has been recorded,
the text output says so honestly instead of printing an empty table, `--json` prints `[]` and
`--csv` prints just the header row — one header serving both kinds, with empty cells wherever a
training row has no order fields (and vice versa):

```text
id,ts,time_utc,kind,character_id,character_name,skill_id,skill_name,finished_level,finish_date,order_id,owner_key,owner_name,type_id,type_name,is_buy,price,volume_total,volume_remain,filled,region_id,location_id,issued,expires,wallet_division,issued_by,backfill,ts_estimated
```

`backfill=1` means the event was read out of ESI's history rather than witnessed between two polls;
`ts_estimated=1` means ESI could not say when the order closed, so the timestamp is the best bound it
has (never later than the order's own expiry). The text view prints those two as `[history]` and
`[time estimated]` on the affected lines.

### `update-data` — alpha caps, skill catalog, blueprint recipes, invention, planetary industry, the planet census and the market type index from the official SDE

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
dashes; `pi` reads the planetary document, `system` reads the census and `market --group` reads the index. The census is deliberately its own
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
published and carry a `marketGroupID` are in it — the SDE's own test for "this is something you can buy" —
which is why `--category Module` names 3,873 types and not the 27,116 published ones.

### `plugins` — what is loaded and who provides each command

```bash
neworks plugins                # one row per plugin, then the commands each one registers
neworks plugins --json         # same, plus hooks and refused plugins, machine-readable
```

Every command comes from a plugin (see *Plugins* under *Repository layout*). The built-in
`characters` plugin has `skills`, `summary`, `plan`, `extract`, `attributes`, `standings`, `travel` and
`implants` (and the consent features attributes, standings, location and clones); `planetary` has `pi`
and `colonies`, the planets consent and the holdings-poll tables; `industry` has `jobs`, `industry`,
`can-build`, `blueprints`, `build-cost`, `restock` and `production`, the jobs and blueprints consents and
the industry tables; `ledger` has the `ledger` command, the wallet consent and the ledger tables. `ledger`
requires `industry` (it costs jobs with industry's recipes), so disabling `industry` also turns the ledger
off; it enhances `planetary`, whose holdings polls a full sync takes only while that plugin is loaded.
A plugin installed as its own distribution adds its commands, sources, panels and hooks the same way,
found through its entry point. Some core results use another plugin's hook when one is loaded: without a
market plugin, `build-cost --sell-at` and `production scan` list at the plain best sell with no seller
tax or margin, and `restock` has no price check; each says so. `legacy` still registers the core's own commands (login, logout, chars, events,
inventory, operation, system, types, doctor, update-data), the built-in `dashboard` plugin has
`dashboard` and its service job, and `core` adds `plugins`, `watch`,
`alerts`, `mcp`, `agent`, `service` and `run` itself. In `--help` the commands
are listed plugin by plugin. To load only some plugins, list them in config.json as
`"plugins": {"enabled": ["legacy"]}` (`core` always loads); without that key every installed plugin loads,
and an unusable value is reported by `doctor` rather than guessed at. Plugins installed as their own
distribution, through the `newedenworks.plugins` entry point group, show up here too. A plugin that
cannot be loaded is listed under *not loaded* with the reason, and every command prints that reason
as a warning on stderr. A plugin is refused when it fails to import, was written for a newer plugin
API, needs a plugin that is missing, or registers a command another plugin already has. The other
plugins still load.

### `service` and `run` — every scheduled job in one process

```bash
neworks service status         # each job: plugin, schedule, on/off, last run and exit code, next slot
neworks service                # run every enabled job on its schedule until stopped (the systemd unit)
neworks service unit           # print a systemd user unit for this checkout and Python
neworks run alerts             # run one job now, under its lock
```

Plugins declare their scheduled work as jobs, and `service` runs all of them from one process instead of a
systemd timer each: `alerts` (core; `alerts run` every 20 minutes, up to a minute late) and `dashboard`
(`dashboard serve`, kept running and restarted 30 s after a crash), plus whatever other plugins add. A job is due once per slot
of its period, counted in UTC (a 20-minute job at :00, :20 and :40, an hourly one on the hour); when the
service starts after a slot the job missed, it runs at once. Each run is a subprocess of its own,
`python -m newedenworks run JOB --scheduled`, so a failing or hanging job never stops the others, and the
service logs one line when each starts and finishes (`journalctl --user -u new-eden-works.service`).

A run takes the job's lock, so two runs of one job never overlap, and records its start, end and exit code
in the state root (`service/JOB.json`). With `--scheduled` it skips a slot in which the job already
started, so the service can run next to another scheduler (a cron line or timer calling `run JOB
--scheduled`): whichever gets to a slot first runs it, and the other logs "already ran in this slot".

`dashboard` is off by default (it opens a port). To turn it on, give config.json the server's settings and
the job:

```json
"service": {"jobs": {"dashboard": true}},
"dashboard": {"host": "0.0.0.0", "port": 8765, "refresh_minutes": 60, "since": "2026-09-19"}
```

The `dashboard` keys are `dashboard serve`'s options: `host`, `port`, `refresh_minutes`,
`orders_refresh_minutes`, `since`, `orders_char`, and `no_sync`, `no_live` and `verbose` as true or false,
plus `pages` (see `dashboard`).
`service.jobs` turns any job on or off the same way (`"alerts": false`); `doctor` reports a name no plugin
declares. The unit is `contrib/systemd/new-eden-works.service`, or `service unit` prints one filled in:

```bash
neworks service unit > ~/.config/systemd/user/new-eden-works.service
loginctl enable-linger "$USER"     # start at boot without a login session (once)
systemctl --user daemon-reload && systemctl --user enable --now new-eden-works.service
journalctl --user -u new-eden-works.service -f     # one line per job start and finish
systemctl --user restart new-eden-works.service    # after an upgrade
```

### `mcp` — the server agents connect to

```bash
neworks mcp --list              # the tools and resources this installation offers
neworks mcp                     # serve MCP over stdin/stdout (what an agent starts)
```

See [Agents: MCP server and skills](#agents-mcp-server-and-skills).

### `doctor` — installation diagnostics

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
before. See [Platform support](#platform-support).

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

---

## Agents: MCP server and skills

New Eden Works does not run a language model of its own. It gives an agent you already use (Claude Code,
Codex, Hermes, Claude Desktop, ...) two things: an MCP server with every command as a tool, and agent
skills that turn recurring requests into tested workflows.

### Setting an agent up

```bash
neworks agent install --target claude-code         # into ./.claude/skills, plus the .mcp.json entry to add
neworks agent install --target claude-code --user  # into ~/.claude/skills instead
neworks agent install --target codex               # ~/.codex/skills and the config.toml table
neworks agent install --target hermes              # ~/.hermes/skills and the config.yaml entry
neworks agent skills                               # the skills the loaded plugins ship
```

`agent install` copies the skill folders (`SKILL.md` and `playbook.example.md`) and prints the MCP server
entry to add to that agent's configuration, with the command that starts this installation's server. For
Claude Code it is a `.mcp.json` in the project (or `claude mcp add`):

```json
{"mcpServers": {"neworks": {"command": "neworks", "args": ["mcp"]}}}
```

Run `agent install` again after an update: it replaces only the folders it installed (they carry a
`.neworks-skill` marker) and leaves a skill of yours with the same name alone unless `--force`.

### The MCP server

`neworks mcp` is an MCP server (standard library only, JSON-RPC over stdio) that every plugin adds to.
Its **tools** are the plugins' commands behind small input schemas (`industry_status`, `industry_jobs`,
`job_board`, `build_cost`, `restock`, `production_supply`, `ledger_progress`, `ledger_pnl`,
`ledger_sql`, `colonies`, `skills`, `operation_show`, `doctor`, ...; `neworks mcp --list` shows them
all). A call runs the command as a child process with `--json` and returns its JSON, so the CLI and the
agent always get the same answer, and warnings the command printed come along. An answer over 1 MB is
refused with a hint to narrow the call. Its **resources** are this installation's operation file and
playbooks (read from the config folder), the ledger's schema, the skills, and
`docs/agent-reference.md` when run from a source checkout. Its **prompts** are the skills, each with
this installation's playbook attached, so an agent that only speaks MCP gets them too.

Tools that change state (`ledger_sync`, `ledger_note_add`, `production_round_save`,
`production_round_handoff`, `alerts_test`) are not offered until config.json allows them by name:

```json
"mcp": {"allow_writes": ["ledger_note_add"]}
```

### The skills

| Skill | Plugin | For requests like | Main commands |
|---|---|---|---|
| `industry-jobs` | industry | "plan the next jobs", "what can I install now", "did I miss anything" | `job-board`: slots, ready jobs, idle blueprints by what they can do, measured times and window-sized runs, a pool check, inputs per facility |
| `supply-run` | industry | "plan a supply run", "what do I need from the hub" | `restock` (needs vs stock, cargo fit, Multibuy) |
| `ops-check` | core | "anything need attention?" | the above, plus `ledger` |

A plugin ships its skills in its package (`Plugin.skills`), so installing a plugin adds its own.

The skills hold what is true for any operation. An installation supplies three things of its own:

- **`operation.json`** (`neworks operation template` prints a starting point): sites, the facility of
  each activity, sellers, planning time zone and finish windows, products never to build, items never to
  sell. The commands read everything about the operation from it, or from flags.
- **A playbook** (`neworks operation playbook`): Markdown files in `<config dir>/playbook/`, one per
  skill, with the operation's own rules - which lines, who builds what, decisions and their reasons. A
  skill reads its playbook first, and **playbook rules win over the skill's defaults**. Each skill folder
  has a `playbook.example.md` with the usual headings.
- **Its own `AGENTS.md`** (or `CLAUDE.md`) for the agent: [docs/AGENTS.example.md](docs/AGENTS.example.md)
  is a neutral starting point.

A rule that holds for every operation goes into the skill through a pull request; one about your
operation goes into your playbook. For scripting against the CLI's JSON or the ledger directly,
[docs/agent-reference.md](docs/agent-reference.md) has the `--json` shapes, the ledger tables and their
traps.

---

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

---

## Where data lives, and how it is protected

### The four roots

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

### Moving from the `eve-skills` folders: `migrate`

Before New Eden Works the folders were named `eve-skills` (`~/.config/eve-skills`,
`%LOCALAPPDATA%\eve-skills\config`, ...). Each kind keeps resolving to its `eve-skills` folder while
that exists and the `new-eden-works` one does not, so an existing installation works unchanged after
an upgrade. `migrate` moves it over:

```bash
neworks migrate                                   # what would be copied (nothing is written)
systemctl --user stop new-eden-works.service      # and stop any `watch`
neworks migrate --apply                           # copy; from now on the new folders are used
systemctl --user start new-eden-works.service
```

- It copies, never moves: the `eve-skills` folders stay exactly as they were, as a backup nobody reads.
  To go back, stop the service and rename the `new-eden-works` folders away; delete the old ones only once
  you no longer want that.
- Each folder is copied into `<name>.migrating` and renamed into place when complete, and the file count
  is checked, so a command never sees half a folder. A ledger with a write-ahead log is copied through
  SQLite's backup, which carries the log; lock files are left behind.
- `--apply` refuses while a service job holds its lock (the dashboard holds one while it runs).
- It also moves the alert settings from `alerts.json` into config.json's `notify`, `industry` and
  `trade.alerts` sections ([alerts](#alerts--industry-job-alerts-in-telegram)), in the new folder only, refusing if any setting would read
  differently; the file stays beside them as `alerts.json.migrated`.
- Absolute paths you wrote yourself (a git remote, a script, `EVE_SKILLS_OPERATION`) still point at
  the old folders: change them by hand.

### The files

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
| `{clone_grades,bloodline_races,skill_catalog,blueprint_materials,planet_industry,system_planets,market_types}.json` | data | SDE snapshot from `update-data`; overrides packaged data. `blueprint_materials.json` is the one `build-cost` reads — every blueprint's activity and material list, keyed by the product it makes; `planet_industry.json` is the one `pi` reads — recipes, structure fitting costs, command-center output per upgrade level and customs tax, all keyed by type id; `system_planets.json` is the one `system` reads — every planet in New Eden counted per solar system by planet type (68,407 planets over 8,088 systems in build 3503375), with a name for each type it met; `market_types.json` is the one `market --group` reads — every market-listed type under its group and its category (19,551 types over 814 groups and 32 categories in build 3494416) | Non-secret |
| `blueprint_invention.json` | data | SDE invention document from `update-data`: per inventable blueprint its data cores per attempt, product and base chance, and the eight generic decryptors' modifiers. The one `ledger` costs invention with | Non-secret |
| `ledger.sqlite3` (+ `-wal`, `-shm`) | data | The accounting ledger: industry jobs, wallet journal and transactions, market orders, blueprint sightings, reference and opening prices, a sync log, per-sync figure snapshots, the operation's notes and to-dos, price tracking (Jita daily history, hourly book snapshots, watchlist changes), and planetary holdings polls. WAL mode, so reports read while a sync writes | **Private business records** (your trades and balances, no credentials). Not regenerable past ESI's 30-day wallet window — back it up like a document |
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
  your platform puts them (on Windows that is one roaming location plus three machine-local ones, so a
  wipe has to visit both roots). `neworks logout` removes tokens locally (all of them, or one with
  `--char`).

Network politeness built in: pinned `X-Compatibility-Date` (`2026-08-18`), a configurable User-Agent,
retries with backoff on 420/429/502/503 honouring `Retry-After`, and automatic pause when ESI's
`X-ESI-Error-Limit-*` headers show the error window filling up.

---

## Development

```bash
git clone https://github.com/kirilan/new-eden-works.git && cd new-eden-works
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -t .                      # the whole suite
uv run --with setuptools python -m unittest discover -s tests -t . -q    # the same with uv
```

The suite (about 1,100 tests) is deterministic: no network and no real credentials. Every test works in
a throwaway `$XDG_*` tree against an in-process fake ESI or fixtures, and those pins are also what let
the Windows branches run on a Linux host. The packaging tier builds a real wheel and sdist and installs
them into a fresh virtualenv; it needs a build backend (`setuptools>=77`), and skips without one. `pytest`
is not a dependency.

CI runs the suite on Ubuntu and Windows with Python 3.11 and 3.14, installs the package and runs its
entry points. A release tag builds the Windows executable with PyInstaller and smoke-tests it on a Windows
runner. What no runner covers is a desktop: double-clicking the `.exe`, SmartScreen, roaming profiles, an
antivirus holding files open. [RELEASE.md](RELEASE.md) is the release procedure.

## Writing a plugin

A plugin is a function returning a `newedenworks.plugin.Plugin`, registered under the
`newedenworks.plugins` entry point group of its own distribution:

```toml
[project.entry-points."newedenworks.plugins"]
myplugin = "my_package:plugin"
```

```python
from newedenworks.plugin import API_VERSION, Command, Plugin

def plugin() -> Plugin:
    return Plugin(name="myplugin", version="0.1.0", api=API_VERSION,
                  summary="what it adds",
                  commands=(Command(("hello",), help="say hello",
                                    run=lambda args, ctx: {"greeting": "o7"},
                                    render=lambda data, args: data["greeting"]),))
```

Install it into the same environment and `neworks plugins` lists it. Besides commands (`run` returns
data, `render` turns it into text, and `--json` comes for free), a `Plugin` can declare: the ESI
consents it needs (`scopes`), its config.json section with defaults and a check (`config`), database
tables with their migrations (`tables`), scheduled jobs for the service (`jobs`), alert sources
(`alerts`), watch sources (`watch`), dashboard panels (`panels`), `doctor` checks (`checks`), MCP tools
(`tools`), hooks other plugins call (`hooks`), agent skills (`skills`), and which plugins it `requires`
or `enhances`. The docstrings in `newedenworks/plugin.py` describe each one; the built-in plugins
(`newedenworks/characters`, `industry`, `planetary`, `ledger`) are complete examples. A plugin written
for a newer `api` than the core knows is refused with a message rather than half-loaded, and a plugin
that fails to load is reported by `neworks plugins` without taking the other commands down.

## Limitations you should know

| Area | Behavior |
|---|---|
| Clone state is inferred | ESI has no "is omega" endpoint. `ALPHA` comes from a live clamp (`active < trained`) — but Expert Systems can also lower active levels on an omega character. `OMEGA` needs an unclamped beyond-cap skill, a catalog-known omega-only skill, or the *currently training* item targeting beyond-cap levels; future queue entries alone yield only `LIKELY_OMEGA`; clamp + omega evidence together is `CONFLICT` (stale ESI data or a just-changed clone); everything within limits is `UNKNOWN`. Skill state that local data cannot confirm never claims omega — it says refresh the SDE. |
| Jove / Triglavian | Have no alpha grade of their own; caps fall back to the union of all four faction grades (highest cap wins). |
| Queue gaps are real data | CCP omits schedule dates when an item cannot train; those rows show `BLOCKED` / "no schedule - cannot train" and must not be read as active training. |
| ESI lags finished training | A completed queue item can stay visible until the character logs in. The tool overlays the completed level and marks it pending (`*`) rather than pretending nothing happened. |
| Data freshness matters | Alpha caps warn after 90 days; extractor rules are dated constants that warn after ~180 days. Both warnings name the remedy or the verification date. |
| The ledger reconstructs what ESI does not say | Job inputs are computed from SDE recipes (no structure rig or hull bonuses: jobs in a player structure are noted), invented-copy decryptors are inferred from copies or runs, and broker fees are matched to orders by timestamp or fee rate. Item-exchange contracts, hauling losses and reprocessing are not booked; contract fees and courier rewards are overhead. Anything the replay had to assume is counted in each report's *assumptions* block. |
| The ledger cannot recover what it never stored | A sync reaches back 30 days of wallet history and ~90 days of jobs. Stock that predates the cutover is valued at the Forge median of the 30 days before it; blueprint copies made before the cutover cost 0. |
| Pagination is capped | Paginated GETs follow at most 100 pages, so an enormous corp asset list would be truncated rather than loop forever. |
| Name resolution degrades | A structure this token may not see stays a labelled `structure <id>`, an id whose parent chain ESI never completes stays `location <id>`; name failures never fail the command. |
| `/universe/names` fails as a whole batch | ESI validates `ids` as int32 and answers **400 for the entire request** when one id overflows (verified live 2026-09-08) — and container, ship and structure ids are all far above that bound. So one citadel in a batch would cost every station name beside it: `inventory` sends type ids to `/universe/types` and location ids to their own resolvers, and never posts a location id to `/universe/names`. The fake ESI reproduces the whole-batch 400, which is what makes "every row is named" a real pin instead of luck. |
| Structure names need consent *and* access | `/universe/structures` answers only with `esi-universe.read_structures.v1`, and only for structures ESI lets this character see; anything else keeps its `structure <id>` cell, and the run prints `login --scopes structures` once. A token that provably lacks the scope is not probed at all — refusals spend error-window budget that throttles the rest of the run. |
| The reference price is not a quote | The default basis is CCP's published figure for an item: nothing can be bought or sold at it, and it does not move with the market. `--value-at` answers the tradable question — the richest standing buy at one scope — and even that is what *dumping* pays; what *listing* would raise is printed beside it from the same rows, never merged into the money column. |
| `--value-at` costs one order book per distinct type | ~0.19 s a type through eight workers measured, so a 518-type holding is 518 requests and a minute and a half cold. The run says so on stderr before it starts; the quote cache then re-serves each figure only while ESI's own `Expires` vouches for it, which is what makes a second look nearly free. |
| Consent is opt-in | A missing optional scope prints `no <feature> consent - run: neworks login --scopes <feature>` for that character and exits 0. Granting consent always requires your click in a browser; the tool will not do it for you. |
| Corp access is role-bound | Corporation jobs/assets need director / Account-Manager rights; ESI refusal becomes a per-character warning. |
| SP history is local and short | 60 days, best-effort, one row per successful fetch, written only when the write succeeds (a failed history write never breaks `skills`). A fresh install honestly reports "no baseline yet" for `--week`. |
| [Planning](#plan--training-plan-one-character) spans attributes | SP costs are exact (rank-based) and prerequisites expand automatically, but one calibrated SP/hour rate prices every row: skills driven by other primary/secondary pairs really train at other rates (the output names the pairs involved), remaps or implant changes during training shift real time, and implants are never modeled into future levels. `plan` needs the SDE skill catalog from `update-data`. |
| Order fill state is derived | ESI's order history only states `cancelled` or `expired`. An order that sold out matches neither, so a filled state is inferred from `volume_remain` reaching zero — and an order ESI explains with no history row at all is reported as `order_closed, reason unknown`, never guessed at. |
| No closure timestamp | ESI publishes no closed-at time. Fills witnessed between two polls are timed at the poll that saw them; closures read out of history are bounded by the order's own expiry and marked `[time estimated]`. In the `--closed` table, `expires` for a cancelled order is when it would have run out, not when it was pulled. |
| Order history is ~90 days | The watch backfill (and the ledger) can only reach ESI's history window; older orders are simply gone from ESI. The first watch poll ingests that whole window silently — those rows are marked `[history]` in `events`, not announced as news. |
| Colony data is only as fresh as the last client view | ESI's own words: *"Planetary information is only recalculated when the colony is viewed through the client."* So `last_update` on a colony can be months old while the extraction timers on it are months out of date; `colonies` marks rows older than 7 days with `*` and says why, and an `extractor_expired` announcement reads "extraction of X finished", never "this extractor is idle", because a player who reprogrammed it minutes ago cannot be contradicted from ESI's stale view. |
| `build-cost` prices the cheapest ask, not the depth behind it | The material column charges a unit at the lowest standing ask in scope; ESI publishes no cumulative quantity per price level, so nothing here knows how many units that ask actually holds. Measured in Jita the error is ~0-1% even at 1000 runs — Tritanium there is effectively bottomless — but a thin market is another matter: the Hound sell side in Heimatar opens with one unit 8.7% below the blended cost of ten, so a quantity that size has to be swept further up a book this tool cannot see and would cost more than printed. |
| Invention is not modelled | A T2 item's real cost is understated: no datacores, no decryptors, no probability-weighted attempts — only the manufacturing run after you already have a blueprint. The SDE carries the inputs for that stage and they were deliberately left out rather than half-modelled, so treat a T2 figure as a floor, not as what invention costs. |
| Blueprint ownership and structure bonuses stay outside the number | BPO/BPC acquisition is excluded — the recipe is priced as if you already own it — industry skills are ignored because they change job time and not materials, and structure or rig bonuses reach the maths only through `--material-multiplier`, which is one flat factor for the whole job rather than the real stack of modifiers. |
| Two research levels, not one per job | `--me` bills the blueprint you run; `--component-me` (default ME 10, since a component's BPO is usually long since researched to the cap) bills every component job. That is two levels where a real build has one per blueprint: nothing below a component job is ever built, so a component's own inputs are bought at their asks whatever you research, and a level named for a recipe that cannot be researched — a reaction — is dropped rather than applied somewhere else. |
| The install fee's rates are CCP policy, not physics | The 4% SCC surcharge and the 0.25% NPC facility tax were measured against CCP's published rules on 2026-09-09 and warn once they are more than 180 days old. `--facility-tax` exists because a player-owned structure bills differently and only you know its rate; the system's cost index is ESI's own, from `/industry/systems`. |
| ESI/SSO availability | Discovery (cached 24 h), token exchange, ESI routes and the SDE download are all remote services; failures surface as `error: network error ...` or `error: HTTP <code> ...`. |
| Desktop notifications are Linux-only in practice | `--notify` shells out to `notify-send`, which no stock Windows installation ships; there the watch says so once per run and relies on the terminal bell. Alerts are printed and recorded on both platforms, so nothing is lost but the ping. |

---

## Repository layout (maintainers)

```text
newedenworks/
  cli.py             the parser built from registered commands, dispatch and main()
  plugin.py          the plugin contract: Plugin, Command, Tool, Job, AlertSource, WatchSource, Panel, Check, Context
  registry.py        plugin discovery (built-ins, then `newedenworks.plugins` entry points), refusal rules, hooks
  legacy.py          the built-in `legacy` plugin: every command from before the split, one configure function each
  cmd_plugins.py     the built-in `core` plugin and its `plugins` command
  store.py           the core store: per-plugin schema versions (plugin_schema), migrations, schema-6 adoption
  config.py          plugin sections of config.json, plugins.enabled, and the problems doctor reports
  characters/        the `characters` plugin (__init__.py: its commands, arguments and consent features)
    skills.py        skills / summary / plan / extract: gather(), the training book, data and text views
    views.py         attributes / standings / travel / implants: data, text and CSV views
    classify.py      alpha-cap lookup, per-skill classification, clone-state inference
    planner.py       rank-based SP costs, prerequisite expansion, rate calibration, extractor math
    snapshots.py     local SP history (60-day JSONL)
  cmd_watch.py       the watch loop over the plugins' watch sources, the core's orders source, desktop
                     notifications, the events command
  service.py         scheduled jobs: `service` (every job in one process), `run JOB`, slots and locks
  notify.py          `alerts`: the Telegram channel, the notify settings, quiet hours, the shared reminder, sources
  mcp.py             `mcp`: the MCP server (stdio JSON-RPC), the tool helpers, resources, prompts, write gating
  agent.py           the plugins' agent skills and `agent install` (Claude Code, Codex, Hermes)
  planetary/         the `planetary` plugin (__init__.py: commands, arguments, planets consent, pi_* tables)
    pi.py            the pi command: recipe-tree quantities, per-step value added and customs, colony fits, planet closure
    colonies.py      the colonies command: live colonies, extractor heads and timers, staleness
  ledger/            the `ledger` plugin (__init__.py: the ledger command, wallet consent, ledger tables; requires industry)
    cmd_ledger.py    the ledger command: sync, pnl, products, markets, invention, inventory, note and progress
    engine.py        the accounting replay: weighted-average costing, job inputs, invention, fee matching
    ledger_sync.py   ESI -> ledger: corporation and member documents, opening prices, watch hook
    ledger_query.py  ledger sql / ledger schema: read-only queries with type_name(), local_time()
    stockpile.py     stock over time: days of stock per finished good, days of cover per input, blocked copies
  cmd_dashboard.py   the dashboard plugin: the web server, its refresh thread, build to JSON
  panels.py          dashboard panels: layout from config, the build context, live reports, the document
  dashboard.py       the page's document parts: ledger figures per period, product and market, live jobs and orders
  ledger_db.py       the ledger's tables, grouped by owning plugin, and idempotent writes of raw ESI rows
  doctor.py          read-only installation diagnostics (never writes, redacts secrets)
  sso.py             OAuth2 PKCE login (loopback + manual), refresh, scope registry, token/config storage
  esi.py             stdlib ESI client: caching, retries, error-limit backoff, server-time, name cache
  universe.py        asset identities: disk-cached type/group/category catalogue + nested location resolver
  market.py          public order books: type/scope resolution, quote maths, cluster scan, freshness,
                     history, and CCP's sales tax / broker fee rates
  industry/          the `industry` plugin (__init__.py: commands, arguments, jobs/blueprints consents, industry tables)
    recipes.py       blueprint recipes, ME/TE and job-time maths, EIV + install fee, one-level build-or-buy
    jobs.py          the jobs command: rows per character/corp, slots, --times, --since (moved out of exports)
    cmd_build_cost.py  the build-cost command: job parameters, build-or-buy forcing, totals and notes
    cmd_restock.py   the restock command: recipe and invention demand, stock netted by place, cargo fit
    job_times.py     measured hours per run from job history and run fitting into finish windows
    cmd_industry.py, industry_status.py   industry status, can-build and blueprints
    job_alerts.py    industry jobs in the Telegram alerts
    production*.py, cmd_production.py   the saved production workflow (rounds, supply, replenish, scan, facility)
  localtime.py       time zones and daily windows (finish windows, quiet hours, local_time())
  orders.py          character/corporation order fetching, normalisation, access (consent vs role) diagnosis
  alphadata.py       packaged/user SDE data loading, transformations, update-data downloader
  watchstate.py      watch observations, exactly-once transitions, durable event history
  storage.py         unique-temp atomic writes + advisory file locks (flock on POSIX, byte-range on Windows)
  paths.py           the only resolver of the config / cache / data / state directories, on either platform
  exports.py         jobs / inventory (grouped, valued) + consent hints (`targets`, `hint`)
  render.py          timestamps, SP/duration/ISK formatting, CSV cells, plain-text tables
  data/              packaged SDE snapshot (clone_grades, bloodline_races, skill_catalog,
                     blueprint_materials, planet_industry, system_planets, market_types,
                     blueprint_invention) and dashboard.html, the dashboard's page
tests/               unittest suite (fake_esi.py is the in-process ESI the integration tests run against)
docs/                agent-reference.md (JSON shapes, ledger tables), production.md, AGENTS.example.md
packaging/windows/   the PyInstaller spec, launcher and README.txt of neworks.exe
contrib/systemd/     new-eden-works.service, the systemd user unit for `neworks service`
pyproject.toml / LICENSE / RELEASE.md   packaging metadata, the GPL-3.0 text, the release procedure
```

### Plugins

A command is a `Command` in some plugin's `commands` (`newedenworks/plugin.py`), not a block in
`cli.py`. The core creates its parser with `help`, passes that to `configure`, and runs either
`handler(args)`, which prints for itself (every `legacy` command), or `run(args, ctx) -> data` with
`render(data, args) -> str`, in which case the core prints the text or, with `--json`, the data
(`csv(data, args)` adds `--csv`, and `watch(args)` takes over when `--watch` is given). `run` never writes
to stdout. When the text needs more than the JSON document holds, it returns a `Result` whose `to_json()`
is the document. A
command path with several words (`("prices", "signal")`) sits under a group: a `Command` with no
`handler` and no `run`, created without help when no plugin registers one. `ctx.hook("ledger.sync_if_due")`
returns another plugin's hook, or `None` when that plugin is not loaded. Built-in plugins are listed
in `registry.BUILTIN` rather than found through entry points, so a source checkout run with
`python -m newedenworks` works without an install.

A plugin also declares:

- `scopes`: its consent features and their ESI scopes (`sso.feature_scopes()` merges them; `login
  --scopes all` asks for every loaded plugin's). Two plugins declaring one feature differently is a clash.
- `tables`: one `Schema` per owner, with its tables and migrations (`store.py`). They live in the one
  ledger file, and `plugin_schema` records each owner's version. Migrations run at open time, the core's
  first. A released step is never edited: a change is a new step at the end.
- `config`: a `ConfigSpec`, the defaults of its config.json section (keyed by the plugin's name) and a
  `validate` that `doctor` runs. A command reads its section as `ctx.config`.
- `checks`: read-only `doctor` checks, shown as `plugin.<plugin>.<check>`.
- `alerts`: `AlertSource(name, collect, features)` for `alerts run` (`notify.py`): `collect(run)` returns
  a `notify.Batch` (what is fresh, what stands for the shared reminder, and `render(reminder)`, the ranked
  title pieces and blocks it adds to the one message); the core composes, sends, then saves each state.
- `panels`: `Panel(id, title, kind, data, prefetch, quick)` for the dashboard (`panels.py`): `id` is
  `<plugin>.<name>`; `data(ctx)` returns a fragment of the page's document (kind `page`, drawn by the
  page's own code) or the data of a generic kind (`kpi`, `table`, `list`, `timeseries`); `prefetch(ctx)`
  starts the live reports it needs so all panels' reports run side by side; a `quick` panel is re-read
  between full rebuilds.
- `skills`: the folder of its agent skills (`agent.py`): one folder per skill with a `SKILL.md`.
- `tools`: `Tool(name, command, args, input_schema, description, read_only, ...)` for `mcp`: the command
  line it runs (`mcp.flags` maps arguments to flags) behind a per-tool schema (`mcp.schema`,
  `mcp.string`, ...); a write tool (`read_only=False`) is offered only when `mcp.allow_writes` names it.
- `watch`: `WatchSource(name, poll, wanted)` for the watch loop (`cmd_watch`): `poll(args, client,
  session)` returns its part of a frame as a `WatchCycle`, `wanted(args)` whether the flags leave it on.
- `jobs`: scheduled work for `service` (`Job(name, every, run, jitter, default)`; `service.command("alerts",
  "run")` makes a job of a CLI command line, `every="daemon"` a process kept running). Job names are
  unique across plugins.

Conventions: standard library only — adding a runtime dependency needs a deliberate decision.
Console entry points are `neworks` and `new-eden-works`, both `newedenworks.cli:main`;
`python -m newedenworks` calls the same function, so every path must stay equivalent. New commands are registered by a plugin (today `legacy.py`), live in a
`cmd_*.py` command area or `exports.py` (consent-gated views), take `--char` with the shared semantics, and degrade per
character rather than aborting a multi-character run. Optional consent always goes through
`sso.OPTIONAL_SCOPES` + `exports.targets()` so a missing grant stays a hint. Keep parser help text,
this README and the scope table in sync when adding a feature name. A new OS difference belongs in
`paths.py` (where a directory lives) or `storage.py` (how a file is locked and replaced), chosen by
capability — module availability, not a version guess — so command handlers never learn the platform
at all.
