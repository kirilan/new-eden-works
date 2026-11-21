"""Finding and loading plugins, and the command table and hooks they register.

Plugins come from two places:

- `BUILTIN`, the plugins inside this package. They are listed in code rather than found through
  entry points, because a source checkout run as `python -m newedenworks` (production's systemd
  units) has no installed metadata, and an editable install only rewrites its entry points on
  reinstall. A built-in plugin must work in both cases.
- The `newedenworks.plugins` entry point group, for plugins installed as their own distribution.

A plugin is refused, with the reason kept for `neworks plugins` and printed as a warning, when it
cannot be imported, does not return a `Plugin`, was written for a newer API, reuses a loaded
plugin's name, lacks a plugin it requires, registers a command, scope feature, schema, table, job,
alert source, watch source or MCP tool another plugin already has, or a panel not named after
itself. The rest still load: one broken plugin does not take the CLI down with it.

`plugins.enabled` in config.json, when present, lists the plugins to load; the others are skipped
as disabled (`core` always loads). Without it every installed plugin loads.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .plugin import API_VERSION, AlertSource, Command, Job, Panel, Plugin, PluginError, Schema, WatchSource

ENTRY_POINT_GROUP = "newedenworks.plugins"

# Built-in plugins as "module:function", loaded in this order before any entry point plugin.
BUILTIN = ("newedenworks.legacy:plugin", "newedenworks.characters:plugin", "newedenworks.planetary:plugin",
           "newedenworks.industry:plugin", "newedenworks.ledger:plugin", "newedenworks.cmd_dashboard:plugin",
           "newedenworks.cmd_plugins:plugin")

# The core's own plugin. Its commands go after every other plugin's in `--help`.
CORE = "core"

# Top-level config.json keys the core uses; a plugin cannot be named like one, since its name is
# also its config section (`config.section`).
RESERVED_NAMES = frozenset({"client_id", "client_secret", "user_agent", "plugins", "notify", "mcp", "service"})


@dataclass(frozen=True)
class Candidate:
    """A plugin before it is loaded: where it comes from and how to get it."""

    source: str                          # "built-in" or "entry point (dist version)"
    target: str                          # "module:function"
    load: Callable[[], Callable[[], Plugin]]


@dataclass(frozen=True)
class Loaded:
    plugin: Plugin
    source: str


@dataclass(frozen=True)
class Refused:
    name: str                            # the plugin's name, or its target when it never loaded
    source: str
    reason: str


def _import_target(target: str) -> Callable[[], Callable[[], Plugin]]:
    module, _, attribute = target.partition(":")
    return lambda: getattr(importlib.import_module(module), attribute)


def builtin_candidates(targets: Iterable[str] = BUILTIN) -> list[Candidate]:
    return [Candidate("built-in", target, _import_target(target)) for target in targets]


def entry_point_candidates() -> list[Candidate]:
    found = []
    for entry in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        dist = getattr(entry, "dist", None)
        label = f"entry point ({dist.name} {dist.version})" if dist is not None else "entry point"
        found.append(Candidate(label, entry.value, entry.load))
    return found


class Registry:
    """The loaded plugins, their commands in parser order, and their hooks."""

    def __init__(self) -> None:
        self.plugins: list[Loaded] = []
        self.refused: list[Refused] = []
        self.disabled: list[Refused] = []    # skipped by plugins.enabled; reason says so
        self.commands: dict[tuple[str, ...], tuple[Command, str]] = {}
        self.hooks: dict[str, Callable[..., Any]] = {}

    # -- look-ups ---------------------------------------------------------------------------

    def plugin(self, name: str) -> Plugin | None:
        return next((loaded.plugin for loaded in self.plugins if loaded.plugin.name == name), None)

    def hook(self, name: str) -> Callable[..., Any] | None:
        """A hook by its full name, "plugin.hook", or None when that plugin is not loaded."""
        return self.hooks.get(name)

    def command(self, path: tuple[str, ...]) -> Command | None:
        entry = self.commands.get(tuple(path))
        return entry[0] if entry else None

    def provider(self, path: tuple[str, ...]) -> str | None:
        entry = self.commands.get(tuple(path))
        return entry[1] if entry else None

    def default_command(self) -> Command | None:
        return next((command for command, _ in self.commands.values() if command.default), None)

    def schemas(self) -> list[Schema]:
        """Every loaded plugin's schemas, in load order."""
        return [schema for loaded in self.plugins for schema in loaded.plugin.tables]

    def scopes(self) -> dict[str, list[str]]:
        """Consent features (what `login --scopes` accepts) and their ESI scopes, from every plugin."""
        merged: dict[str, list[str]] = {}
        for loaded in self.plugins:
            for feature, scopes in loaded.plugin.scopes.items():
                merged.setdefault(feature, list(scopes))
        return merged

    def jobs(self) -> list[tuple[Job, str]]:
        """Every loaded plugin's jobs with the plugin's name, in load order."""
        return [(job, loaded.plugin.name) for loaded in self.plugins for job in loaded.plugin.jobs]

    def job(self, name: str) -> tuple[Job, str] | None:
        return next((entry for entry in self.jobs() if entry[0].name == name), None)

    def alert_sources(self) -> list[tuple[AlertSource, str]]:
        """Every loaded plugin's alert sources with the plugin's name, in load order."""
        return [(source, loaded.plugin.name) for loaded in self.plugins for source in loaded.plugin.alerts]

    def panels(self) -> list[tuple[Panel, str]]:
        """Every loaded plugin's dashboard panels with the plugin's name, in load order."""
        return [(panel, loaded.plugin.name) for loaded in self.plugins for panel in loaded.plugin.panels]

    def watch_sources(self) -> list[tuple[WatchSource, str]]:
        """Every loaded plugin's watch sources with the plugin's name, in load order."""
        return [(source, loaded.plugin.name) for loaded in self.plugins for source in loaded.plugin.watch]

    def ordered_commands(self) -> list[tuple[Command, str]]:
        """Commands in the order the parser lists them: plugin load order, the core's last."""
        entries = list(self.commands.values())
        return [e for e in entries if e[1] != CORE] + [e for e in entries if e[1] == CORE]

    # -- loading ----------------------------------------------------------------------------

    def _add(self, plugin: Plugin, source: str) -> None:
        if plugin.name in RESERVED_NAMES:
            raise PluginError(f"{plugin.name} is a core config key and cannot be a plugin name")
        clashes = [c.name for c in plugin.commands if tuple(c.path) in self.commands]
        if clashes:
            owners = sorted({self.commands[tuple(c.path)][1] for c in plugin.commands
                             if tuple(c.path) in self.commands})
            raise PluginError(f"command {', '.join(clashes)} is already registered by {', '.join(owners)}")
        defaults = [c.name for c in plugin.commands if c.default]
        if defaults and (self.default_command() is not None or len(defaults) > 1):
            raise PluginError(f"a second default command ({', '.join(defaults)})")
        for command in plugin.commands:
            if not command.path or not all(isinstance(word, str) and word for word in command.path):
                raise PluginError(f"command path {command.path!r} is not a tuple of words")
            if command.run is not None and command.handler is not None:
                raise PluginError(f"command {command.name} has both run and handler")
            if command.run is not None and command.render is None:
                raise PluginError(f"command {command.name} has run but no render")
        known_scopes = self.scopes()
        for feature, scopes in plugin.scopes.items():
            if feature in known_scopes and sorted(known_scopes[feature]) != sorted(scopes):
                raise PluginError(f"scope feature {feature} is already declared with other scopes")
        owned = {schema.name for schema in self.schemas()}
        tables = {table for schema in self.schemas() for table in schema.tables}
        for schema in plugin.tables:
            if schema.name in owned or schema.name == CORE:
                raise PluginError(f"schema {schema.name} is already owned by another plugin")
            if not schema.migrations:
                raise PluginError(f"schema {schema.name} has no migrations")
            taken = sorted(set(schema.tables) & tables)
            if taken:
                raise PluginError(f"table {', '.join(taken)} is already owned by another plugin")
        from . import service
        names = {job.name for job, _ in self.jobs()}
        for job in plugin.jobs:
            service.validate(job)
            if job.name in names:
                raise PluginError(f"job {job.name} is already declared by {self.job(job.name)[1]}")
            names.add(job.name)
        from .panels import GENERIC_KINDS, PAGE
        for panel in plugin.panels:
            if not panel.id.startswith(plugin.name + ".") or panel.id == plugin.name + ".":
                raise PluginError(f"panel {panel.id} must be named {plugin.name}.<name>")
            if panel.kind not in (PAGE, *GENERIC_KINDS):
                raise PluginError(f"panel {panel.id} has kind {panel.kind!r}; known: {', '.join((PAGE, *GENERIC_KINDS))}")
        import re
        known_tools = {tool.name: loaded.plugin.name for loaded in self.plugins for tool in loaded.plugin.tools}
        for tool in plugin.tools:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool.name or ""):
                raise PluginError(f"tool name {tool.name!r} must be lower-case letters, digits and underscores")
            if tool.name in known_tools:
                raise PluginError(f"tool {tool.name} is already declared by {known_tools[tool.name]}")
            if not tool.command:
                raise PluginError(f"tool {tool.name} names no command")
            known_tools[tool.name] = plugin.name
        alerting = {alert.name: owner for alert, owner in self.alert_sources()}
        for alert in plugin.alerts:
            if alert.name in alerting:
                raise PluginError(f"alert source {alert.name} is already declared by {alerting[alert.name]}")
            alerting[alert.name] = plugin.name
        watched = {watch.name: owner for watch, owner in self.watch_sources()}
        for watch in plugin.watch:
            if watch.name in watched:
                raise PluginError(f"watch source {watch.name} is already declared by {watched[watch.name]}")
            watched[watch.name] = plugin.name
        for command in plugin.commands:
            self.commands[tuple(command.path)] = (command, plugin.name)
        for hook_name, function in plugin.hooks.items():
            self.hooks[f"{plugin.name}.{hook_name}"] = function
        self.plugins.append(Loaded(plugin, source))


def _declared(candidate: Candidate) -> Plugin:
    factory = candidate.load()
    plugin = factory()
    if not isinstance(plugin, Plugin):
        raise PluginError(f"{candidate.target} returned {type(plugin).__name__}, not a Plugin")
    return plugin


def _supported(plugin: Plugin) -> None:
    if not isinstance(plugin.api, int) or plugin.api < 1:
        raise PluginError(f"plugin {plugin.name} declares no valid api version ({plugin.api!r})")
    if plugin.api > API_VERSION:
        raise PluginError(f"plugin {plugin.name} {plugin.version} needs plugin API {plugin.api}; "
                          f"this core supports up to {API_VERSION} - upgrade new-eden-works")


def _dependency_order(plugins: list[tuple[Plugin, str]]) -> tuple[list[tuple[Plugin, str]], dict[str, str]]:
    """Each plugin after what it requires and, where that makes no loop, what it enhances; otherwise as found.

    Returns the order and, separately, the plugins caught in a `requires` cycle with the cycle spelled
    out; those are left out of the order rather than loaded in a guessed one. `enhances` never makes a
    cycle: a plugin may enhance one that requires it (industry enhances the ledger, which requires it),
    and such an edge is simply not used for ordering."""
    by_name = {plugin.name: (plugin, source) for plugin, source in plugins}
    requires = {name: [d for d in plugin.requires if d in by_name] for name, (plugin, _) in by_name.items()}

    # 1. Cycles among hard requirements.
    cyclic: dict[str, str] = {}
    state: dict[str, str] = {}

    def check(name: str, chain: tuple[str, ...]) -> bool:
        if state.get(name) == "done":
            return name not in cyclic
        if state.get(name) == "visiting":
            loop = chain[chain.index(name):] + (name,)
            for member in loop:
                cyclic.setdefault(member, "plugin dependency cycle: " + " -> ".join(loop))
            return False
        state[name] = "visiting"
        for dependency in requires[name]:
            if not check(dependency, chain + (name,)):
                cyclic.setdefault(name, f"requires {dependency}, which is in a dependency cycle")
        state[name] = "done"
        return name not in cyclic

    for name in by_name:
        check(name, ())

    def required_by_closure(start: str) -> set[str]:
        seen, stack = set(), [start]
        while stack:
            for dependency in requires[stack.pop()]:
                if dependency not in seen:
                    seen.add(dependency)
                    stack.append(dependency)
        return seen

    # 2. Order: requirements first, then soft dependencies that do not require their way back.
    edges = {name: list(requires[name]) + [d for d in by_name[name][0].enhances
                                           if d in by_name and name not in required_by_closure(d)]
             for name in by_name}
    ordered: list[tuple[Plugin, str]] = []
    placed: set[str] = set()

    def place(name: str) -> None:
        if name in placed or name in cyclic:
            return
        placed.add(name)
        for dependency in edges[name]:
            place(dependency)
        ordered.append(by_name[name])

    for name in by_name:
        place(name)
    return ordered, cyclic


_CURRENT: Registry | None = None


def current() -> Registry:
    """The registry this process runs with, loaded on first use (built-ins, entry points, config)."""
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = load()
    return _CURRENT


def set_current(registry: Registry | None) -> None:
    """Replace the process registry (tests); None loads it afresh on next use."""
    global _CURRENT
    _CURRENT = registry


def load(candidates: Iterable[Candidate] | None = None, enabled: Iterable[str] | None | bool = True) -> Registry:
    """Load the candidate plugins (by default the built-ins, then installed entry points).

    `enabled` is the set of plugin names to load; True (the default) reads `plugins.enabled` from
    config.json, and None loads every candidate."""
    if candidates is None:
        candidates = [*builtin_candidates(), *entry_point_candidates()]
    if enabled is True:
        from . import config
        enabled = config.enabled_plugins()
    enabled = None if enabled is None else set(enabled) | {CORE}
    registry = Registry()
    declared: list[tuple[Plugin, str]] = []
    for candidate in candidates:
        try:
            plugin = _declared(candidate)
        except Exception as exc:          # a third-party plugin can fail in any way at import
            if candidate.source == "built-in":
                raise                     # a broken built-in is a bug in this package: fail loudly
            registry.refused.append(Refused(candidate.target, candidate.source, _reason(exc)))
            continue
        try:
            _supported(plugin)
        except PluginError as exc:
            registry.refused.append(Refused(plugin.name, candidate.source, str(exc)))
            continue
        if enabled is not None and plugin.name not in enabled:
            registry.disabled.append(Refused(plugin.name, candidate.source, "not in plugins.enabled"))
            continue
        if any(plugin.name == other.name for other, _ in declared):
            registry.refused.append(Refused(plugin.name, candidate.source,
                                            f"a plugin named {plugin.name} is already loaded"))
            continue
        declared.append((plugin, candidate.source))

    # Requirements, repeated until stable: refusing one plugin can strand another that required it.
    while True:
        names = {plugin.name for plugin, _ in declared}
        stranded = [(plugin, source) for plugin, source in declared
                    if any(required not in names for required in plugin.requires)]
        if not stranded:
            break
        for plugin, source in stranded:
            missing = [required for required in plugin.requires if required not in names]
            registry.refused.append(Refused(plugin.name, source, "requires " + ", ".join(missing)
                                            + " (not installed or refused)"))
        declared = [entry for entry in declared if entry not in stranded]

    ordered, cyclic = _dependency_order(declared)
    for plugin, source in declared:
        if plugin.name in cyclic:
            registry.refused.append(Refused(plugin.name, source, cyclic[plugin.name]))
    for plugin, source in ordered:
        if any(required not in {loaded.plugin.name for loaded in registry.plugins}
               for required in plugin.requires):
            missing = [r for r in plugin.requires if registry.plugin(r) is None]
            registry.refused.append(Refused(plugin.name, source, "requires " + ", ".join(missing)
                                            + " (refused)"))
            continue
        try:
            registry._add(plugin, source)
        except PluginError as exc:
            registry.refused.append(Refused(plugin.name, source, str(exc)))
    return registry


def _reason(exc: BaseException) -> str:
    if isinstance(exc, PluginError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
