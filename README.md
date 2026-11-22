# New Eden Works

New Eden Works (`neworks`) is a command line toolkit for running an EVE Online operation from your own
machine: characters and skill training, industry jobs and production planning, planetary industry, an
accounting ledger of what the business cost and earned, and a web dashboard. Each area is a plugin you
can switch on or off. An MCP server and a set of agent skills let AI agents (Claude Code, Codex, Hermes
and others) use the same tools.

- **Standard library only.** Python 3.11+ on Linux and Windows, no runtime dependencies, nothing to
  compile. Windows users can also take a standalone `neworks.exe`.
- **Read-only against ESI.** It never spends ISK, moves anything, trains skills or changes the game.
- **Multi-character.** One login per character; every command walks all stored characters unless you
  pick one with `--char`.
- **Runs on your machine.** No hosted service: tokens, the ledger and settings stay in your profile.
- Text output by default, `--json` / `--csv` for scripts, `--watch` for a live terminal view.

```console
$ neworks summary                         # one line per character
$ neworks skills --char Somecharacter     # skills, queue and clone state
$ neworks industry status --corp          # slots, jobs, idle blueprints and stock in one view
$ neworks build-cost Hound                # build it or buy it, priced off the live order books
$ neworks ledger pnl --by week            # what the operation earned
$ neworks dashboard serve                 # the web dashboard on http://localhost:8765/
```

## Quick start

**1. Install** with Python 3.11+ and [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/):

```bash
pipx install git+https://github.com/kirilan/new-eden-works.git
# or: uv tool install git+https://github.com/kirilan/new-eden-works.git
neworks --version
```

On Windows without Python, download `new-eden-works-<version>-windows-x64.zip` from the
[releases page](https://github.com/kirilan/new-eden-works/releases) and run `neworks.exe` from where you
extracted it. [Installing](docs/install.md) covers the other ways, including working from a checkout.

**2. Download the game data** (~100 MB, once, and again after a game patch). Skill plans, build costs,
planetary industry and restock lists read recipes and skills from it:

```bash
neworks update-data
```

**3. Register an EVE application** at <https://developers.eveonline.com/applications>: callback URL
exactly `http://localhost:8635/callback`, and tick every scope in the
[scope table](docs/login.md#eve-developer-application). Copy its **Client ID**.

**4. Log in**, once per character. A browser opens; pick the character and approve:

```bash
neworks login --client-id <your-client-id> --scopes all   # the client ID is remembered afterwards
neworks login --scopes all                                 # the next character
```

On a machine reached over SSH, add `--manual` and paste the URL the browser ends on back into the
terminal ([details](docs/login.md#remote-host-ssh----manual)).

**5. Check the installation** with `neworks doctor`, which prints the fix for anything that is not ok.
Then give ESI a contact address, as CCP asks tools to: add
`"user_agent": "new-eden-works (you@example.com)"` to `config.json` ([where it is](#where-your-data-lives)).

**6. Use it:**

```bash
neworks summary                       # one line per character
neworks jobs --slots                  # industry jobs and free slots per character
neworks colonies --detail             # planetary colonies and when each extractor runs out
neworks ledger sync && neworks ledger pnl   # book wallet and jobs into the ledger, then the profit
```

`neworks <command> --help` shows any command's options. A feature whose consent a character has not
granted prints the `login` that adds it and carries on; [which commands need which
consent](docs/login.md#which-commands-need-which-consent).

## What's included

| Area | Commands | What it does |
|---|---|---|
| [Characters](docs/characters.md) | `skills` (the default), `summary`, `attributes`, `standings`, `travel`, `implants`, `plan`, `extract` | Skills, training queue and clone state; attributes, standings, location and clones; priced training plans and Skill Extractor math |
| [Industry](docs/industry.md) | `jobs`, `blueprints`, `can-build`, `industry status`, `job-board`, `build-cost`, `restock` | Jobs and slots, measured run times, blueprints, jobs that stock allows, build-or-buy costs, cargo-fitted shopping lists |
| [Production](docs/production.md) | `production` | Saved rounds of jobs, shared material pools, supply forecasts, replenishment and product scans |
| [Planetary industry](docs/planetary.md) | `pi`, `colonies` | Recipe trees, colony fits and planet yields; your live colonies and extractor timers |
| [Ledger](docs/ledger.md) | `ledger` | What the operation cost, earned and holds: profit and loss, products, invention, stock value, notes, to-dos and read-only SQL |
| [Dashboard](docs/dashboard.md) | `dashboard` | Profit, planetary industry, stockpile and industry slots on a local web page |
| [Inventory](docs/inventory.md) | `inventory` | Assets named, placed and valued |
| [Items and systems](docs/game-data.md) | `types`, `system`, `update-data` | Type ids and packaged volume; system security, planets and jumps; the game data download |
| [Operation file](docs/operation.md) | `operation` | Sites, facilities, structure markets, freight, planning windows and the playbook |
| [Watch, alerts, service](docs/automation.md) | `watch`, `events`, `alerts`, `service`, `run` | Live terminal view, recorded events, Telegram job alerts, every scheduled job in one process |
| [Agents](docs/agents.md) | `mcp`, `agent` | The MCP server and the agent skills |
| [Login](docs/login.md) | `login`, `logout`, `chars` | EVE SSO, consent per feature, stored characters |
| [Diagnostics](docs/data-and-security.md#doctor--installation-diagnostics) | `doctor` | Installation, logins and data freshness, never writing anything |
| [Plugins](docs/plugins.md) | `plugins` | What is loaded and which plugin gives each command |

The commands come from plugins: `neworks plugins` lists the loaded ones and which plugin gives each
command, and `plugins.enabled` in `config.json` picks which load (all of them when absent). Other
plugins install as their own Python distributions; see [Writing a plugin](docs/plugins.md#writing-a-plugin).

## Configuration

Everything works without configuration beyond the login. Two files describe your setup when you want
the planning commands to know it:

- **`config.json`** (config folder): the client ID, `user_agent`, and sections for alerts (`notify`,
  `industry`), `dashboard`, `service`, `mcp` and `plugins`. `neworks doctor` reports a value it cannot use.
- **`operation.json`** (config folder): sites, facilities and their bonuses, extra hubs, freight
  routes, finish windows and products never to build. Start from `neworks operation template` and check
  it with `neworks operation show`; see [the operation file](docs/operation.md).

## Running it continuously

One service runs every scheduled job: Telegram alerts for industry jobs every 20 minutes and, when
turned on, the dashboard.

```bash
neworks alerts setup --token-file token.txt     # Telegram bot token from @BotFather
neworks service unit > ~/.config/systemd/user/new-eden-works.service
systemctl --user daemon-reload && systemctl --user enable --now new-eden-works.service
neworks service status                          # each job, its last run and next slot
```

See [watch, alerts and the service](docs/automation.md).

## Agents

New Eden Works runs no language model of its own. It gives an agent you already use an MCP server with
every command as a tool, and skills for recurring requests (`industry-jobs`, `supply-run`, `ops-check`):

```bash
neworks agent install --target claude-code   # skills into ./.claude/skills, plus the .mcp.json entry
neworks mcp --list                           # the tools and resources this installation offers
```

Codex and Hermes are supported too. See [agents](docs/agents.md), and
[agent-reference.md](docs/agent-reference.md) for the `--json` shapes and ledger tables.

## Where your data lives

| Kind | Linux | Windows |
|---|---|---|
| config (`tokens.json`, `config.json`, `operation.json`) | `~/.config/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\config` |
| data (game data, `ledger.sqlite3`) | `~/.local/share/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\data` |
| state (watch state, events) | `~/.local/state/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\state` |
| cache | `~/.cache/new-eden-works` | `%LOCALAPPDATA%\new-eden-works\cache` |

`$XDG_CONFIG_HOME` and its siblings override these on both platforms. `tokens.json` holds live
credentials; the ledger keeps wallet history that ESI forgets after 30 days, so back it up. See
[where data lives](docs/data-and-security.md).

## Documentation

- [Installing](docs/install.md): uv, pip, the Windows executable, platform differences
- [EVE application and login](docs/login.md): scopes, manual login, consent, several characters
- [Characters](docs/characters.md), [industry](docs/industry.md), [production](docs/production.md),
  [planetary industry](docs/planetary.md), [ledger](docs/ledger.md), [dashboard](docs/dashboard.md),
  [inventory](docs/inventory.md), [items and systems](docs/game-data.md),
  [the operation file](docs/operation.md), [watch, alerts and the service](docs/automation.md)
- [Agents](docs/agents.md) and the [agent reference](docs/agent-reference.md)
- [Where data lives, and diagnostics](docs/data-and-security.md)
- [Limitations](docs/limitations.md): what the numbers can and cannot know
- [Plugins](docs/plugins.md) and [development](docs/development.md); [RELEASE.md](RELEASE.md) for releases
- [Moving from eve-skills](docs/migrating-from-eve-skills.md), for installations older than 0.2.0

## Development

```bash
git clone https://github.com/kirilan/new-eden-works.git && cd new-eden-works
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -t .
```

The suite needs no network and no credentials. See [development](docs/development.md) for CI, the
repository layout and conventions.

## License

GPL-3.0-only; see [LICENSE](LICENSE).
