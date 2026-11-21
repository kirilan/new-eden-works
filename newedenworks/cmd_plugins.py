"""The core plugin: its `plugins` command (which plugins are loaded and what each provides), and
`watch` over the plugins' watch sources, and `service` and `run` for their scheduled jobs (`newedenworks.service`)."""

from __future__ import annotations

import os

from . import __version__, agent, cmd_watch, mcp, migrate, notify, render, service
from .plugin import API_VERSION, Command, Job, Plugin, Tool
from .registry import CORE


def plugins_data(args, ctx) -> dict:
    registry = ctx.registry
    loaded = []
    for entry in registry.plugins:
        plugin = entry.plugin
        loaded.append({
            "name": plugin.name,
            "version": plugin.version,
            "api": plugin.api,
            "source": entry.source,
            "summary": plugin.summary,
            "requires": list(plugin.requires),
            "enhances": list(plugin.enhances),
            "commands": [command.name for command in plugin.commands],
            "hooks": sorted(f"{plugin.name}.{name}" for name in plugin.hooks),
            "scopes": sorted(plugin.scopes),
            "schemas": [{"name": schema.name, "version": schema.version, "tables": list(schema.tables)}
                        for schema in plugin.tables],
            "checks": [check.name for check in plugin.checks],
            "jobs": [{"name": job.name, "every": job.every} for job in plugin.jobs],
            "watch": [source.name for source in plugin.watch],
            "alerts": [source.name for source in plugin.alerts],
            "panels": [panel.id for panel in plugin.panels],
            "config": plugin.config is not None,
        })
    return {
        "api": API_VERSION,
        "plugins": loaded,
        "refused": [{"name": r.name, "source": r.source, "reason": r.reason} for r in registry.refused],
        "disabled": [{"name": d.name, "source": d.source} for d in registry.disabled],
    }


def plugins_text(data: dict, args) -> str:
    rows = [[p["name"], p["version"], p["source"], str(len(p["commands"])), p["summary"]] for p in data["plugins"]]
    blocks = [render.table(["plugin", "version", "source", "commands", "summary"], rows)]
    for p in data["plugins"]:
        lines = [f"{p['name']}: " + (", ".join(p["commands"]) or "no commands")]
        if p["requires"]:
            lines.append("  requires " + ", ".join(p["requires"]))
        if p["enhances"]:
            lines.append("  enhances " + ", ".join(p["enhances"]))
        if p["hooks"]:
            lines.append("  hooks " + ", ".join(p["hooks"]))
        if p["scopes"]:
            lines.append("  consent features " + ", ".join(p["scopes"]))
        for schema in p["schemas"]:
            lines.append(f"  tables ({schema['name']} v{schema['version']}) " + ", ".join(schema["tables"]))
        if p["checks"]:
            lines.append("  doctor checks " + ", ".join(p["checks"]))
        if p["panels"]:
            lines.append("  dashboard panels " + ", ".join(p["panels"]))
        if p["alerts"]:
            lines.append("  alert sources " + ", ".join(p["alerts"]))
        if p["watch"]:
            lines.append("  watch sources " + ", ".join(p["watch"]))
        if p["jobs"]:
            lines.append("  jobs " + ", ".join(f"{j['name']} ({j['every']})" for j in p["jobs"]))
        blocks.append("\n".join(lines))
    if data["disabled"]:
        blocks.append("disabled (not in plugins.enabled): " + ", ".join(d["name"] for d in data["disabled"]))
    if data["refused"]:
        blocks.append("not loaded:\n" + "\n".join(f"  {r['name']} ({r['source']}): {r['reason']}"
                                                  for r in data["refused"]))
    return "\n\n".join(blocks)


# The agent skills the core ships: ops-check reads every plugin's tools.
SKILLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


def plugin() -> Plugin:
    return Plugin(
        name=CORE,
        skills=SKILLS,
        version=__version__,
        api=API_VERSION,
        summary="the harness itself: plugin loading and the commands that manage it",
        commands=(Command(("plugins",), help="loaded plugins, the commands each one provides, and any "
                                              "plugin that was refused and why",
                          run=plugins_data, render=plugins_text),
                  Command(("watch",), help="keep polling every watch source (training, orders, colonies) and "
                                           "announce what changes; Ctrl-C stops",
                          configure=cmd_watch._watch_args, handler=cmd_watch.cmd_watch),
                  notify.COMMAND,
                  mcp.COMMAND,
                  agent.COMMAND,
                  migrate.COMMAND,
                  *service.COMMANDS),
        # What eve-orders-alert.timer runs (every 20 minutes, up to a minute late): every alert source.
        tools=(
            Tool("plugins", command=("plugins",), title="Plugins",
                 description="Loaded plugins and what each provides; refused plugins and why.",
                 input_schema=mcp.schema(), args=mcp.flags()),
            Tool("service_status", command=("service",), title="Service status",
                 description="Each scheduled job: schedule, on or off, last run and exit code, next slot.",
                 input_schema=mcp.schema(), args=mcp.flags("status")),
            Tool("alerts_preview", command=("alerts", "run"), title="Alerts preview", json=False,
                 description="The Telegram message the next alerts run would send (a dry run: nothing is "
                             "sent or saved).",
                 input_schema=mcp.schema(), args=mcp.flags("--dry-run")),
            Tool("alerts_test", command=("alerts", "test"), title="Telegram test", read_only=False,
                 idempotent=False, json=False,
                 description="Send one line to the alerts chat.",
                 input_schema=mcp.schema(text=mcp.string("message text")), args=mcp.flags(text=None)),
        ),
        jobs=(Job("alerts", every="20m", jitter="1m", run=service.command("alerts", "run"),
                  help="Telegram alerts from every alert source (orders, industry jobs)"),),
    )
