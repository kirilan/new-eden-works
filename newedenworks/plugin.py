"""The plugin contract: what a New Eden Works plugin registers with the core.

A plugin is a function returning a `Plugin`. The core finds it either in its own list of built-in
plugins or through the `newedenworks.plugins` entry point group, then loads it if its API version is
supported and every plugin it `requires` is present (`newedenworks.registry`).

A plugin can register commands, hooks, database tables, ESI consent scopes, a config.json section,
doctor checks, scheduled jobs, alert sources, watch sources, dashboard panels, MCP tools and agent
skills. `resources` is declared for later use and not read yet.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# The plugin API this core implements. A plugin declares the version it was written against; one
# written for a newer API is refused with a message rather than half-loaded.
API_VERSION = 1


class PluginError(Exception):
    """A plugin that cannot be loaded: bad declaration, missing requirement or a clash."""


@dataclass(frozen=True)
class Tool:
    """An MCP tool (`newedenworks.mcp`): one CLI command line behind a small input schema.

    `command` are the command's words (`("industry", "status")`) and `args(arguments) -> [str]` the rest
    of the line built from the tool's validated arguments (`mcp.flags` covers the usual mapping). The
    server runs it as a child process, with `--json` unless `json` is False, so the CLI and MCP never
    answer differently. Input schemas are written per tool, a few clear parameters rather than every
    flag. A tool with `read_only=False` changes state and is offered only once config.json lists it in
    `mcp.allow_writes`; `destructive` and `idempotent` become MCP's hints."""

    name: str
    read_only: bool = True
    input_schema: Mapping[str, Any] | None = None
    description: str | None = None
    command: tuple[str, ...] = ()
    args: Callable[[dict], list[str]] | None = None
    title: str | None = None
    destructive: bool = False
    idempotent: bool = True
    json: bool = True


@dataclass(frozen=True)
class Command:
    """One CLI command, `neworks <path...>`.

    `path` is the command's words: ("industry",) or ("prices", "signal"). A command whose path has
    more than one word sits under a group, which is a command with neither `handler` nor `run`; the
    core creates a group without help text when no plugin registers one.

    The core creates the argparse parser with `help` and passes it to `configure`. Then, either:

    - `handler(args) -> int | None`: computes and prints on its own (every command before the
      plugin split works this way), or
    - `run(args, ctx) -> data` with `render(data, args) -> str`: returns data and never writes to
      stdout (a warning may go to stderr). The core prints it, and adds `--json` when `configure`
      did not; MCP will call `run` directly. `data` is a JSON document, or a `Result` when the text
      view needs more than the document holds.

    `csv(data, args) -> str` adds a CSV view (`--csv`, which wins over `--json`). `watch(args)` is a
    long-running loop the core hands over to when the parsed arguments carry a true `watch`.
    `default` marks the command that runs when no command is given (`skills` today).
    """

    path: tuple[str, ...]
    help: str | None = None
    configure: Callable[[argparse.ArgumentParser], None] | None = None
    handler: Callable[[argparse.Namespace], int | None] | None = None
    run: Callable[[argparse.Namespace, "Context"], Any] | None = None
    render: Callable[[Any, argparse.Namespace], str] | None = None
    csv: Callable[[Any, argparse.Namespace], str] | None = None
    watch: Callable[[argparse.Namespace], int | None] | None = None
    tool: Tool | None = None           # unused: tools are declared on the plugin (`Plugin.tools`)
    default: bool = False

    @property
    def name(self) -> str:
        return " ".join(self.path)

    @property
    def is_group(self) -> bool:
        return self.handler is None and self.run is None


class Result:
    """What a command's `run` returns when its text view needs more than its JSON document holds.

    `to_json()` is the document (`--json`, and what MCP returns); `render` and `csv` receive the
    whole object."""

    def to_json(self) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class Job:
    """Work `neworks service` runs on a schedule; `neworks run JOB` runs it by hand.

    `every` is a period such as "20m", "1h" or "1d" ("s" for seconds too). The job runs once in each
    slot of that length counted in UTC from the epoch, so a 20m job is due at :00, :20 and :40 and a
    1h job on the hour, up to `jitter` (same units) later to spread the load. When the service starts
    after the slot the job last ran in has passed, it runs at once: a missed run is caught up, as with
    a systemd timer's Persistent=true. `every="daemon"` is a long-running process instead (the
    dashboard's web server), started with the service and started again 30 s after it fails.

    `run(ctx) -> exit code | None` does the work; the service runs it in a subprocess of its own, so
    one failing job cannot stop the others (`newedenworks.service.command` turns a CLI command line
    into one). A job with `default=False` stays off until config.json's `service.jobs` turns it on;
    `service.jobs` can turn any job off the same way."""

    name: str
    every: str
    run: Callable[["Context"], Any]
    jitter: str = ""
    help: str = ""
    default: bool = True


@dataclass(frozen=True)
class AlertSource:
    """A plugin's part of `alerts run` (`newedenworks.notify`).

    `collect(run) -> notify.Batch` reads what it needs (ESI through `run.client`, the characters in
    `run.chars`) and decides what is news against its own state; the core decides the shared reminder,
    composes the one message and sends it, and only then calls the batch's `save`. `features(settings)
    -> [(consent feature, ESI scope)]` are the logins it reads with, asked for in one `exports.targets`."""

    name: str
    collect: Callable[..., Any]
    features: Callable[[Any], list] | None = None


@dataclass(frozen=True)
class WatchSource:
    """One subject of the watch loop (`neworks watch`, `skills --watch`, `orders --watch`).

    `poll(args, client, session) -> newedenworks.cmd_watch.WatchCycle` takes one turn: what it
    observed (claimed against the stored watch state, so each transition is announced once), its
    warnings, and its part of the frame. It must not raise for an ESI or network problem, which is a
    warning: an overnight watch survives outages. `wanted(args)` says whether the parsed arguments
    leave it on (`--no-orders`); without it the source always runs. `session.sources` names every
    source of the run, so a source can show more when it watches alone."""

    name: str
    poll: Callable[..., Any]
    wanted: Callable[[Any], bool] | None = None


@dataclass(frozen=True)
class Panel:
    """A dashboard panel (`newedenworks.panels`). `id` is "<plugin>.<name>".

    `data(ctx) -> dict` returns the panel's part of the document. Kind `page` is a built-in panel the
    page has its own code for: the dict is merged into the page's document. Kinds `kpi`, `table`,
    `list` and `timeseries` are drawn generically from the dict alone:

    - kpi: {"tiles": [{"label", "value", "note"}]}
    - table: {"columns": [name, ...], "rows": [[cell, ...], ...]}
    - list: {"items": [text, ...]}
    - timeseries: {"points": [{"period", "value"}], "label": str}

    `prefetch(ctx)` starts the live reports `data` will wait for (`ctx.start(...)`), so every panel's
    reports run side by side. A `quick` panel is also read between full rebuilds (the server's
    --orders-refresh). `settings` are the defaults of its entry in the dashboard config."""

    id: str
    title: str
    kind: str
    data: Callable[..., Any]
    settings: Mapping[str, Any] | None = None
    prefetch: Callable[..., None] | None = None
    quick: bool = False


@dataclass(frozen=True)
class Check:
    """A `neworks doctor` check: `run(ctx) -> {"status": "ok"|"warn"|"fail"|"skip", "detail": str,
    "hint": str (optional)}`, or a list of those. It is shown as `plugin.<plugin>.<name>`, and must
    not write anything: doctor is read-only."""

    name: str
    run: Callable[..., Any]


@dataclass(frozen=True)
class Schema:
    """Tables a plugin owns in the core store, with their migrations in order (version 1 first).

    `name` is the key the store records the version under (table plugin_schema), normally the
    plugin's own name. A script should use IF NOT EXISTS, so a step interrupted half way can run
    again. A released step is never edited: a change is a new step at the end."""

    name: str
    migrations: tuple[Any, ...]      # each an SQL script, or a function given the open connection
    tables: tuple[str, ...] = ()

    @property
    def version(self) -> int:
        return len(self.migrations)


@dataclass(frozen=True)
class ConfigSpec:
    """A plugin's section of config.json: its defaults and a check of what the user wrote.

    `validate(section) -> [problem]` gets the section merged over the defaults and returns what is
    wrong with it, in words a user can act on; `doctor` reports them."""

    defaults: Mapping[str, Any] = field(default_factory=dict)
    validate: Callable[[dict], list[str]] | None = None


@dataclass(frozen=True)
class Plugin:
    """Everything one plugin offers. `name` is its CLI help group, config section and hook prefix."""

    name: str
    version: str
    api: int
    summary: str = ""
    requires: tuple[str, ...] = ()     # will not load without these
    enhances: tuple[str, ...] = ()     # calls their hooks when present, carries on without them
    scopes: Mapping[str, list[str]] = field(default_factory=dict)
    config: ConfigSpec | None = None
    commands: tuple[Command, ...] = ()
    jobs: tuple[Job, ...] = ()
    alerts: tuple[AlertSource, ...] = ()
    watch: tuple[WatchSource, ...] = ()
    panels: tuple[Panel, ...] = ()
    tables: tuple[Schema, ...] = ()
    checks: tuple[Check, ...] = ()
    tools: tuple[Tool, ...] = ()       # MCP tools (`newedenworks.mcp`)
    hooks: Mapping[str, Callable[..., Any]] = field(default_factory=dict)
    skills: str | None = None
    resources: tuple[Any, ...] = ()


class Context:
    """What the core hands to everything a plugin registers.

    It carries the plugin registry, the plugin it is for, and that plugin's config section. The ESI
    client, characters and price lookup join it as plugins move out of `legacy` and need them.
    """

    def __init__(self, registry, plugin: str | None = None) -> None:
        self.registry = registry
        self.plugin = plugin

    @property
    def config(self) -> dict:
        """This plugin's config.json section over its declared defaults ({} for none)."""
        from . import config
        return config.section(self.registry.plugin(self.plugin)) if self.plugin else {}

    def hook(self, name: str) -> Callable[..., Any] | None:
        """`ctx.hook("trade.price_signal")`: the function, or None when its plugin is not loaded."""
        return self.registry.hook(name)
