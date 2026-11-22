# Plugins

Every command comes from a plugin. This page covers seeing and choosing what is loaded, writing a plugin,
and the full plugin contract.

## `plugins` — what is loaded and who provides each command

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
found through its entry point. Some core results use another plugin's hook when one is loaded
(`build-cost --sell-at` and `production scan` for the seller's tax and fees, `restock` for its price
check); without it they say what is missing. `legacy` still registers the core's own commands (login, logout, chars, events,
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

## The plugin contract

A command is a `Command` in some plugin's `commands` (`newedenworks/plugin.py`), not a block in
`cli.py`. The core creates its parser with `help`, passes that to `configure`, and runs either
`handler(args)`, which prints for itself (every `legacy` command), or `run(args, ctx) -> data` with
`render(data, args) -> str`, in which case the core prints the text or, with `--json`, the data
(`csv(data, args)` adds `--csv`, and `watch(args)` takes over when `--watch` is given). `run` never writes
to stdout. When the text needs more than the JSON document holds, it returns a `Result` whose `to_json()`
is the document. A
command path with several words (`("myplugin", "report")`) sits under a group: a `Command` with no
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
