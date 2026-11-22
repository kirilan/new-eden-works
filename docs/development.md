# Development

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
antivirus holding files open. [RELEASE.md](../RELEASE.md) is the release procedure.

## Conventions

- Standard library only: adding a runtime dependency needs a deliberate decision.
- Console entry points are `neworks` and `new-eden-works`, both `newedenworks.cli:main`;
  `python -m newedenworks` calls the same function, so every path must stay equivalent.
- New commands are registered by a plugin (see [plugins](plugins.md)), take `--char` with the shared
  semantics, and degrade per character rather than aborting a multi-character run.
- Optional consent always goes through `sso.OPTIONAL_SCOPES` + `exports.targets()` so a missing grant
  stays a hint. Keep parser help text and the docs (the README's command table, the scope table in
  [login.md](login.md)) in sync when adding a feature name.
- A new OS difference belongs in `paths.py` (where a directory lives) or `storage.py` (how a file is
  locked and replaced), chosen by capability — module availability, not a version guess — so command
  handlers never learn the platform at all.

## Repository layout

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
