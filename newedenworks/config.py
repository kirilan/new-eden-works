"""Plugin sections of config.json, and which plugins are enabled.

config.json (in the config directory, see `paths`) holds the core's settings (client id and secret,
user agent) and, from the plugin harness on, one section per plugin keyed by its name, plus
`plugins.enabled`. A plugin declares its section with a `ConfigSpec`: defaults, and a `validate`
that `doctor` runs. Nothing here writes the file; `sso.save_config` stays its only writer.
"""

from __future__ import annotations

from typing import Any

from . import sso
from .plugin import Plugin

def _file() -> dict:
    cfg = sso.load_config()
    return cfg if isinstance(cfg, dict) else {}


def enabled_plugins(cfg: dict | None = None) -> list[str] | None:
    """`plugins.enabled` as a list of names, or None (load everything) when absent or unusable.

    An unusable value is reported by `problems`, never guessed at: loading nothing because of a typo
    would take every command away, including the ones needed to fix it."""
    cfg = _file() if cfg is None else cfg
    value = (cfg.get("plugins") or {}).get("enabled") if isinstance(cfg.get("plugins"), dict) else None
    if isinstance(value, list) and all(isinstance(name, str) for name in value):
        return list(value)
    return None


def service_jobs(cfg: dict | None = None) -> dict[str, Any]:
    """config.json's `service.jobs`: job name -> true or false, overriding each job's default."""
    cfg = _file() if cfg is None else cfg
    service = cfg.get("service")
    jobs = service.get("jobs") if isinstance(service, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def section(plugin: Plugin | None, cfg: dict | None = None) -> dict[str, Any]:
    """The plugin's section over its defaults; {} for a plugin without a ConfigSpec."""
    if plugin is None:
        return {}
    cfg = _file() if cfg is None else cfg
    defaults = dict(plugin.config.defaults) if plugin.config is not None else {}
    stored = cfg.get(plugin.name)
    return {**defaults, **stored} if isinstance(stored, dict) else defaults


def problems(registry, cfg: dict | None = None) -> list[tuple[str, str]]:
    """What is wrong in config.json, as (owner, message) pairs: `plugins` for the enabled list, a
    plugin's name for its own section."""
    cfg = _file() if cfg is None else cfg
    found: list[tuple[str, str]] = []
    plugins = cfg.get("plugins")
    if plugins is not None:
        value = plugins.get("enabled") if isinstance(plugins, dict) else None
        if not isinstance(plugins, dict) or ("enabled" in plugins and enabled_plugins(cfg) is None):
            found.append(("plugins", "plugins.enabled must be a list of plugin names; every installed "
                                     "plugin loads until it is"))
        elif isinstance(value, list):
            known = {loaded.plugin.name for loaded in registry.plugins} | {d.name for d in registry.disabled}
            known |= {r.name for r in registry.refused}
            unknown = sorted(set(value) - known)
            if unknown:
                found.append(("plugins", f"plugins.enabled names {', '.join(unknown)}, which is not installed"))
    service = cfg.get("service")
    if service is not None:
        jobs = service.get("jobs") if isinstance(service, dict) else None
        if not isinstance(service, dict) or ("jobs" in service and not isinstance(jobs, dict)):
            found.append(("service", "service must be an object and service.jobs an object of job name: true/false"))
        elif isinstance(jobs, dict):
            known = {job.name for job, _ in registry.jobs()}
            for name, value in jobs.items():
                if name not in known:
                    found.append(("service", f"service.jobs names {name}, which no loaded plugin declares"))
                elif not isinstance(value, bool):
                    found.append(("service", f"service.jobs.{name} must be true or false"))
    from . import notify                   # the alert settings: notify, industry's and trade.alerts' keys
    found.extend(notify.problems(cfg))
    mcp_section = cfg.get("mcp")
    if mcp_section is not None:
        writes = mcp_section.get("allow_writes") if isinstance(mcp_section, dict) else None
        if not isinstance(mcp_section, dict) or ("allow_writes" in mcp_section and not isinstance(writes, list)):
            found.append(("mcp", "mcp must be an object and mcp.allow_writes a list of tool names"))
        elif isinstance(writes, list):
            tools = {tool.name: tool for loaded in registry.plugins for tool in loaded.plugin.tools}
            for name in writes:
                if name not in tools:
                    found.append(("mcp", f"mcp.allow_writes names {name}, which no loaded plugin offers as a tool"))
                elif tools[name].read_only:
                    found.append(("mcp", f"mcp.allow_writes names {name}, which is read-only and always offered"))
    if "dashboard" in cfg and registry.plugin("dashboard") is None:
        from . import cmd_dashboard        # the service's dashboard job reads it as `dashboard serve` options
        try:
            cmd_dashboard.serve_argv(cfg["dashboard"])
        except RuntimeError as exc:
            found.append(("dashboard", str(exc)))
    for loaded in registry.plugins:
        plugin = loaded.plugin
        stored = cfg.get(plugin.name)
        if stored is not None and not isinstance(stored, dict):
            found.append((plugin.name, f"the {plugin.name} section must be an object"))
            continue
        if plugin.config is not None and plugin.config.validate is not None:
            try:
                messages = plugin.config.validate(section(plugin, cfg))
            except Exception as exc:  # noqa: BLE001 - a broken validator is a finding, not a crash
                messages = [f"its config check failed: {type(exc).__name__}: {exc}"]
            found.extend((plugin.name, message) for message in messages or [])
    return found
