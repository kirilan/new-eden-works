"""The `legacy` plugin: every command from before the plugin split, registered unchanged.

The core builds its parser from registered commands. This built-in plugin registered every command
when the plugin split began; the others have since moved to the `characters`, `planetary`, `industry`
and `ledger` plugins, and what is left here are the core's own commands (logins, look-ups, inventory,
the operation file, diagnostics and data updates).
"""

from __future__ import annotations

import time

from . import (__version__, alphadata, doctor as doctor_mod, exports, market, render, sso,
               watchstate)
from . import cmd_operation, cmd_system, cmd_types, cmd_watch
from .characters import FEATURES as CHARACTER_FEATURES
from .industry import FEATURES as INDUSTRY_FEATURES
from .ledger import FEATURES as LEDGER_FEATURES
from .planetary import FEATURES as PLANETARY_FEATURES
from .plugin import API_VERSION, Command, Plugin, Tool, WatchSource
from .mcp import flags, schema, string, strings


def cmd_login(args):
    scopes = (["attributes"] if args.attributes else []) + [s.strip() for s in (args.scopes or "").split(",") if s.strip()]
    # EVE SSO binds one authorization to one character - there is no bulk consent. Repeating the
    # flow in-process is the next best thing: the browser keeps the SSO session, so each extra
    # character costs a character pick, not another password.
    count = max(1, getattr(args, "repeat", 1) or 1)
    stored = []
    for index in range(count):
        if count > 1:
            print(f"\n--- character {index + 1} of {count} ---")
            print("Pick a DIFFERENT character on the consent screen. Same EVE account = no password "
                  "retyping; a character on another account needs that account's login first.")
        try:
            record = sso.login(client_id=args.client_id, client_secret=args.client_secret, port=args.port,
                               scopes=scopes, manual=args.manual)
        except RuntimeError as exc:
            if not stored:
                raise
            print(f"stopped after {len(stored)}: {exc}")
            break
        stored.append(record)
        print(f"Logged in as {record['character_name']} ({record['character_id']}).")
    if count == 1:
        print("Run neworks login again (selecting a different character) to add another, "
              "or pass --repeat N to chain several in one go.")
    else:
        print(f"\n{len(stored)} of {count} stored: " + ", ".join(r["character_name"] or "?" for r in stored))


def cmd_logout(args):
    char_id = sso.resolve_character(args.char) if args.char else None
    sso.clear_tokens(char_id)
    print(f"Removed stored tokens for {args.char}." if char_id else "Removed stored tokens for all characters.")


def cmd_chars(args):
    records = sso.list_characters()
    if not records:
        print("no characters logged in — run: neworks login")
        return
    rows = []
    for r in records:
        left_min = max(int((r["expires_at"] - time.time()) / 60), 0)
        rows.append([str(r["character_id"]), r.get("character_name") or "?", f"{left_min} min", "yes" if r.get("refresh_token") else "no"])
    print(render.table(["character id", "name", "access token left", "auto-refresh"], rows))


def cmd_update_data(args):
    summary = alphadata.update(build=args.build)
    print(f"Updated to SDE build {summary['build']}:")
    for race, g in sorted(summary["grades"].items()):
        print(f"  {g['name']}: {g['skills']} alpha-trainable skills")
    print(f"  skill catalog: {summary['catalog_skills']} skills (name, rank, attributes, prerequisites)")
    print(f"  blueprint materials: {summary['blueprint_products']} products a manufacturing or reaction "
          f"blueprint builds (materials, run time, batch limit)")
    print(f"  planetary industry: {summary['pi_schematics']} schematics across "
          f"{summary['pi_planet_types']} planet types, {summary['pi_commodities']} commodities and "
          f"{summary['pi_command_center_levels']} command center levels (facility costs, output rates, customs tax)")
    print(f"  planet census: {summary['census_systems']} solar systems, {summary['census_planets']} planets "
          f"over {summary['census_planet_types']} planet types (per-system planet counts for "
          f"neworks system)")
    print(f"  market type index: {summary['market_types']} market-listed types in "
          f"{summary['market_groups']} groups across {summary['market_categories']} categories "
          f"(the lists behind neworks market --group / --category)")
    print(f"  invention: {summary['invention_blueprints']} inventable blueprints and "
          f"{summary['decryptors']} decryptors (data cores per attempt and decryptor modifiers for "
          f"neworks ledger)")


# -- argument setup, one function per command, as split out of the old cli.build_parser --

def _login(p_login):
    p_login.add_argument("--client-id", help="application client id from https://developers.eveonline.com/applications")
    p_login.add_argument("--client-secret", help="only for confidential-type app registrations")
    p_login.add_argument("--port", type=int, help="exact loopback callback port (must match the registered redirect URL; default tries 8635-8637)")
    p_login.add_argument("--manual", action="store_true", help="paste the localhost callback URL manually (for remote hosts reached over ssh)")
    p_login.add_argument("--attributes", action="store_true", help="include character attributes (already covered by the standard skills consent)")
    p_login.add_argument("--repeat", type=int, default=1, metavar="N",
                         help="run the login flow N times in a row, for N characters. EVE issues one token per "
                              "character, so this is N consent screens - but one command, and the browser keeps "
                              "the SSO session between them")
    p_login.add_argument("--scopes", help="extra consents, comma-separated: attributes,standings,jobs,assets,location,clones,orders,corp-orders,structures,structure-search,structure-markets,planets,blueprints,divisions,wallet,all (re-authenticates the chosen character with EXACTLY these consents, so pass every one you still want - or just 'all')")


def _logout(p_logout):
    p_logout.add_argument("--char", help="only this character (name or id); default removes all")


def _events(p_events):
    p_events.add_argument("--char", help="stored character name or id; a bare numeric id also matches logged-out characters")
    p_events.add_argument("--owner", metavar="VALUE",
                          help="only order events of one owner: its exact key (char:90000001, "
                               "corp:98000001) or part of its name, case-insensitive - corporation "
                               "events carry no character id, so this is how you find them")
    p_events.add_argument("--kind", action="append", metavar="K",
                          help=f"only this event kind; repeatable: {', '.join(watchstate.EVENT_KINDS)}")
    p_events.add_argument("--limit", type=int, default=50, metavar="N", help="most recent N events (default 50)")
    p_events.add_argument("--json", action="store_true", help="machine-readable output")
    p_events.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the table")


def _inventory(p_inv):
    p_inv.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_inv.add_argument("--corp", action="store_true", help="corporation assets instead of personal (needs the matching director/Account-Manager role)")
    p_inv.add_argument("--by", choices=["location", "category", "division"], default="location",
                       help="group the summary by location (default), category, or corporation division")
    p_inv.add_argument("--division", metavar="N|NAME",
                       help="with --corp, only one hangar division by number or player-defined name")
    p_inv.add_argument("--items", action="store_true", help="list every item row instead of the grouped summary, most valuable first")
    p_inv.add_argument("--value-at", dest="value_at", metavar="HUB|REGION",
                       help=f"value holdings at the richest standing buy order there instead of "
                            f"ESI's published reference price; a hub reads that station only: "
                            f"{', '.join(market.HUBS)}, or a region by exact name or id")
    p_inv.add_argument("--json", action="store_true", help="machine-readable output: ids and names together, with the valuation basis")
    p_inv.add_argument("--csv", action="store_true", help="full CSV rows on stdout (always per-item); the valuation footnotes go to stderr")


def _operation(p_operation):
    p_operation.add_argument("action", nargs="?", choices=["show", "template", "playbook"], default="show",
                             help="show what is configured (default), print a template to start from, or "
                                  "list / print this installation's playbook (its own rules for the agent "
                                  "skills, Markdown files in <config dir>/playbook or $NEWORKS_PLAYBOOK)")
    p_operation.add_argument("name", nargs="?", metavar="NAME",
                             help="with playbook: print NAME.md (a missing file is not an error)")
    p_operation.add_argument("--path", action="store_true",
                             help="with playbook NAME: print only the file's path")
    p_operation.add_argument("--json", action="store_true", help="machine-readable output")


def _system(p_system):
    p_system.add_argument("systems", nargs="+", metavar="SYSTEM",
                          help="exact system name or numeric id, e.g. Rens or 30002510; several are "
                               "printed as one comparison table")
    p_system.add_argument("--route", metavar="HUB",
                          help=f"add a jumps column to one of the trade hubs ({', '.join(market.HUBS)}) "
                               f"or to any other named system")
    p_system.add_argument("--flag", choices=cmd_system.ROUTE_FLAGS, default="shortest", metavar="FLAG",
                          help="which route the jumps column reports: shortest takes any system on the "
                               "way, secure avoids lowsec and nullsec, insecure also crosses wormholes; "
                               "default shortest. The other of shortest/secure is fetched as well, and a "
                               "difference between the two is reported rather than hidden")
    p_system.add_argument("--json", action="store_true",
                          help="machine-readable report: both security figures, the planet breakdown and "
                               "every route fetched")
    p_system.add_argument("--csv", action="store_true", help="CSV rows - one per system - on stdout")


def _types(p_types):
    p_types.add_argument("names", nargs="+", metavar="NAME|ID",
                         help="exact item names (case aside) or type ids; '-' reads names from stdin, one per "
                              "line, and accepts the in-game paste format name<TAB>quantity (adds an m3 total). "
                              "An unknown name is reported and makes the exit status 1; the others still print")
    p_types_out = p_types.add_mutually_exclusive_group()
    p_types_out.add_argument("--json", action="store_true", help="machine-readable output")
    p_types_out.add_argument("--csv", action="store_true", help="CSV rows on stdout (unknown names go to stderr)")


def _doctor(p_doctor):
    p_doctor.add_argument("--json", action="store_true", help="machine-readable report")
    p_doctor.add_argument("--network", action="store_true",
                          help="also probe EVE SSO discovery and public ESI endpoints (unauthenticated, bounded)")
    p_doctor.add_argument("--timeout", type=float, default=doctor_mod.NET_TIMEOUT, metavar="SECONDS",
                          help=f"per-request network timeout for --network probes (default {doctor_mod.NET_TIMEOUT:g})")


def _update_data(p_update):
    p_update.add_argument("--build", type=int, help="specific SDE build number (default: latest)")


COMMANDS = (
    Command(("login",), help="authorize via EVE SSO (opens browser)",
            configure=_login, handler=cmd_login),
    Command(("logout",), help="remove stored tokens",
            configure=_logout, handler=cmd_logout),
    Command(("chars",), help="list logged-in characters",
            configure=None, handler=cmd_chars),
    Command(("events",), help="show recorded watch events (training, market orders and planetary extractions)",
            configure=_events, handler=cmd_watch.cmd_events),
    Command(("inventory",), help="asset inventory with real item names, grouped by location or category and valued (needs login --scopes assets)",
            configure=_inventory, handler=exports.cmd_inventory),
    Command(("operation",), help=("sites, facilities, extra hubs and freight from the operation file "
                                  "(operation.json in the config directory)"),
            configure=_operation, handler=cmd_operation.cmd_operation),
    Command(("system",), help=("true security status, region and planets of one or more solar "
                               "systems, with jumps to a hub (planet census from local SDE data, "
                               "the rest from public ESI, no login)"),
            configure=_system, handler=cmd_system.cmd_system),
    Command(("types",), help=("type id, exact name, group, category and packaged m3 for item names "
                              "or ids"),
            configure=_types, handler=cmd_types.cmd_types),
    Command(("doctor",), help="diagnose installation, stored logins and data freshness (never writes)",
            configure=_doctor, handler=doctor_mod.cmd_doctor),
    Command(("update-data",), help="refresh alpha caps, the skill catalog and blueprint materials from the official SDE (~100 MB download)",
            configure=_update_data, handler=cmd_update_data),
)


TOOLS = (
    Tool("operation_show", command=("operation",), title="Operation",
         description="The operation file: sites, the facility of each activity, extra hubs, freight routes, "
                     "sellers, the pool holder, planning (time zone, finish windows) and selling rules. "
                     "Values marked NOT SET are defaults, not facts.",
         input_schema=schema(), args=flags("show")),
    Tool("playbook", command=("operation",), title="Playbook", json=False,
         description="This installation's own rules for an agent skill; they win over the skill's defaults.",
         input_schema=schema(required=("skill",), skill=string("skill name, e.g. ops-check")),
         args=flags("playbook", skill=None)),
    Tool("types", command=("types",), title="Item types",
         description="Type id, exact name, group, category and packaged volume for item names or ids.",
         input_schema=schema(required=("names",), names=strings("item names or ids")), args=flags(names=None)),
    Tool("doctor", command=("doctor",), title="Doctor",
         description="Installation, logins, data freshness and plugin checks (read-only).",
         input_schema=schema(), args=flags()),
)


def plugin() -> Plugin:
    return Plugin(
        name="legacy",
        version=__version__,
        api=API_VERSION,
        summary="logins, item and system look-ups, inventory, the operation file, diagnostics and data updates",
        commands=COMMANDS,
        tools=TOOLS,
        # The core's own orders accessor: `skills --watch` polls orders whether or not trade is installed.
        watch=(WatchSource("orders", poll=cmd_watch.orders_poll, wanted=cmd_watch.orders_wanted),),
        # Until each area moves out: the remaining consent features (the core's own: skills consent aside,
        # assets, location of structures, orders read by the core accessor, divisions).
        scopes={feature: scopes for feature, scopes in sso.OPTIONAL_SCOPES.items()
                if feature not in CHARACTER_FEATURES + PLANETARY_FEATURES + INDUSTRY_FEATURES
                + LEDGER_FEATURES},
    )
