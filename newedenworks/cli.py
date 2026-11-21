"""The New Eden Works command line: the parser built from registered commands, and main().

Commands come from plugins (`newedenworks.registry`). Today one built-in plugin, `legacy`
(`newedenworks/legacy.py`), registers every command from before the plugin split, and the core adds
`plugins`. A new command is a `Command` in a plugin's `commands`, not a parser block here."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, esi as esi_mod, registry as registry_mod
from .plugin import Command, Context, Result


def build_parser(registry: registry_mod.Registry | None = None) -> argparse.ArgumentParser:
    """The whole command surface: every command the loaded plugins register, in their order."""
    registry = registry if registry is not None else registry_mod.current()
    parser = argparse.ArgumentParser(
        prog="neworks",
        description="New Eden Works: EVE Online characters, industry, planetary industry, the ledger and more, "
                    "as plugins (neworks plugins lists them).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    groups: dict[tuple[str, ...], argparse._SubParsersAction] = {(): sub}
    for command, _provider in registry.ordered_commands():
        _add_command(command, groups, registry)
    return parser


def group_dest(path: tuple[str, ...]) -> str:
    """The Namespace attribute holding the word chosen under the group at `path`."""
    return "_".join(path).replace("-", "_") + "_action"


def _add_command(command: Command, groups: dict, registry: registry_mod.Registry) -> None:
    path = tuple(command.path)
    if path in groups:            # already created as the implicit group of an earlier command
        return
    parent = _group(path[:-1], groups, registry)
    p = parent.add_parser(path[-1], help=command.help)
    if command.configure is not None:
        command.configure(p)
    if command.run is not None and not any("--json" in a.option_strings for a in p._actions):
        p.add_argument("--json", action="store_true", help="machine-readable output")
    if command.csv is not None and not any("--csv" in a.option_strings for a in p._actions):
        p.add_argument("--csv", action="store_true", help="CSV rows on stdout")
    if command.is_group and len(path) and any(len(other) > len(path) and other[:len(path)] == path
                                             for other in registry.commands):
        groups[path] = p.add_subparsers(dest=group_dest(path), required=True, metavar="ACTION")


def _group(path: tuple[str, ...], groups: dict, registry: registry_mod.Registry) -> argparse._SubParsersAction:
    if path in groups:
        return groups[path]
    parent = _group(path[:-1], groups, registry)
    p = parent.add_parser(path[-1])
    groups[path] = p.add_subparsers(dest=group_dest(path), required=True, metavar="ACTION")
    return groups[path]


def resolve(args: argparse.Namespace, registry: registry_mod.Registry) -> Command | None:
    """The command the parsed arguments chose, following group words down to the leaf."""
    path: tuple[str, ...] = (args.command,)
    while True:
        command = registry.command(path)
        if command is not None and not command.is_group:
            return command
        word = getattr(args, group_dest(path), None)
        if word is None:
            return command
        path = path + (word,)


def run_command(command: Command, args: argparse.Namespace, registry: registry_mod.Registry) -> int:
    """Run one command: a legacy handler prints for itself; a data command's result is printed here."""
    if command.handler is not None:
        # Handlers return None for the ordinary success path; doctor reports its own exit code.
        return int(command.handler(args) or 0)
    if command.watch is not None and getattr(args, "watch", None):
        return int(command.watch(args) or 0)
    data = command.run(args, Context(registry, registry.provider(command.path)))
    if command.csv is not None and getattr(args, "csv", False):
        sys.stdout.write(command.csv(data, args))
    elif getattr(args, "json", False):
        print(json.dumps(data.to_json() if isinstance(data, Result) else data, indent=2))
    else:
        text = command.render(data, args)
        if text:
            print(text)
    return 0


def _use_utf8_streams() -> None:
    """Make our own output encoding-independent, whatever the console was configured with.

    Everything this tool writes is UTF-8: the token store, the event history, ESI's JSON and the
    ``--json`` reports that round-trip it. A character name is whatever the player typed and EVE
    accounts are global, so one legitimately contains CJK or Cyrillic - and a Windows stdout that
    is not a console (piped to another program, redirected to a file, captured by CI) encodes with
    the ANSI code page, cp1252 on an English install, where those characters do not exist. The
    command then did all its work and failed on the printing: ``UnicodeEncodeError`` and exit 1 for
    a report that was fine. An interactive Windows console already speaks UTF-8 (PEP 528), so this
    changes nothing for a person at a keyboard; it only stops the redirect from corrupting the run.

    Streams with no ``reconfigure`` - a test's ``StringIO``, an already-replaced stdout - are left
    exactly as they were found.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):     # closed, detached, or not a text stream after all
            pass


def _warn_refused(registry: registry_mod.Registry) -> None:
    for refused in registry.refused:
        print(f"warning: plugin {refused.name} ({refused.source}) not loaded: {refused.reason}",
              file=sys.stderr)


def main(argv=None):
    _use_utf8_streams()      # before parsing: an argparse usage error is output too
    registry = registry_mod.current()
    _warn_refused(registry)
    parser = build_parser(registry)
    args = parser.parse_args(argv)
    if args.command is None:
        default = registry.default_command()
        if default is None:
            parser.print_help()
            return 0
        args = parser.parse_args([*default.path] + (argv or []))
    command = resolve(args, registry)
    try:
        return run_command(command, args, registry)
    except (RuntimeError, esi_mod.EsiError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
