"""The `industry` plugin: jobs, blueprints, build costs, restock lists and the saved production workflow.

Commands: jobs, industry status, can-build, blueprints, build-cost, restock and production, with the
jobs and blueprints consents and the industry tables. Still handlers that print (step 8's MCP reads
them through --json). The ledger costs jobs with this plugin's recipes, job reads and facility
history; it declares that dependency when it moves.
"""

from __future__ import annotations

import os

from .. import __version__, market, sso
from ..plugin import API_VERSION, AlertSource, Command, Plugin, Schema
from . import alert_source, board, panels, tools, cmd_build_cost, cmd_industry, cmd_production, cmd_restock, jobs
from . import recipes as industry

# This plugin's consent features (what `login --scopes` accepts), taken from sso.OPTIONAL_SCOPES.
FEATURES = ("jobs", "blueprints")

# Jobs and blueprint sightings, and the production workflow's saved rounds, pool handoffs and immutable
# facility history. Version 1 is exactly these tables as eve-skills schema 6 had them.
INDUSTRY_V1 = """
-- One row per industry job, whoever owns it. `owner` is 'corp:<id>' or 'char:<id>'. Status,
-- successful_runs and completed_date change while ESI still serves the job, so they are refreshed.
CREATE TABLE IF NOT EXISTS jobs (
    job_id            INTEGER PRIMARY KEY,
    owner             TEXT NOT NULL,
    activity_id       INTEGER NOT NULL,
    status            TEXT,
    installer_id      INTEGER,
    facility_id       INTEGER,
    blueprint_id      INTEGER,
    blueprint_type_id INTEGER,
    product_type_id   INTEGER,
    runs              INTEGER NOT NULL,
    licensed_runs     INTEGER,
    successful_runs   INTEGER,
    probability       REAL,
    cost              REAL,
    start_date        TEXT NOT NULL,
    end_date          TEXT,
    completed_date    TEXT,
    raw               TEXT NOT NULL,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL
);

-- Every blueprint seen by a sync. An invented copy's ME/TE is how the ledger learns which decryptor
-- made it; ESI's job rows never say.
CREATE TABLE IF NOT EXISTS blueprints (
    item_id    INTEGER PRIMARY KEY,
    owner      TEXT NOT NULL,
    type_id    INTEGER NOT NULL,
    quantity   INTEGER,
    me         INTEGER,
    te         INTEGER,
    runs       INTEGER,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS blueprints_type ON blueprints (type_id);

CREATE TABLE IF NOT EXISTS production_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    document TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS production_active_site ON production_rounds(site) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS pool_handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES production_rounds(id),
    facility_id INTEGER NOT NULL,
    holder_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    hangar TEXT NOT NULL,
    note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facility_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id INTEGER NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    profile TEXT NOT NULL,
    UNIQUE(facility_id, effective_at)
);

CREATE TRIGGER IF NOT EXISTS facility_history_no_update BEFORE UPDATE ON facility_history
BEGIN SELECT RAISE(ABORT, 'facility history is immutable; append a dated correction'); END;

CREATE TRIGGER IF NOT EXISTS facility_history_no_delete BEFORE DELETE ON facility_history
BEGIN SELECT RAISE(ABORT, 'facility history is immutable'); END;
"""

INDUSTRY_TABLES = ("jobs", "blueprints", "production_rounds", "pool_handoffs", "facility_history")
SCHEMA = Schema("industry", (INDUSTRY_V1,), INDUSTRY_TABLES)


# -- argument setup, as moved from the legacy plugin ----------------------------------------------

def _jobs(p_jobs):
    p_jobs.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_jobs.add_argument("--corp", action="store_true", help="corporation industry jobs instead of personal (needs the matching director/Account-Manager role)")
    p_jobs.add_argument("--completed", action="store_true", help="include finished and cancelled jobs")
    p_jobs.add_argument("--group", action="store_true",
                        help="collapse identical jobs by installer, status, activity, product and runs")
    p_jobs.add_argument("--slots", action="store_true",
                        help="show manufacturing, science and reaction slots used / maximum / free per character")
    p_jobs.add_argument("--times", action="store_true",
                        help="median measured hours per run by activity, product, installer and "
                             "facility (reads finished jobs too)")
    p_jobs.add_argument("--since", metavar="HH:MM|ISO",
                        help="jobs installed since then, delivered ones included, grouped by installer, "
                             "activity, facility, product and runs: 'did I miss anything'. HH:MM is the most "
                             "recent such time in --tz (default: the operation file's planning.timezone, "
                             "else UTC)")
    p_jobs.add_argument("--days", type=int, default=14, metavar="N",
                        help="with --times: only jobs started in the last N days (default 14)")
    p_jobs.add_argument("--facility", metavar="NAME",
                        help="with --times: only jobs installed in this facility of the operation file "
                             "(times are always reported per facility)")
    p_jobs.add_argument("--site", metavar="NAME",
                        help="with --times: hours per run where this operation-file site does each "
                             "activity (its facilities); jobs measured at another facility of the file are "
                             "converted by the time multipliers and marked ~, others are listed but never "
                             "scaled. With --times or --since: reads the site's characters (a corp site: "
                             "the corporation)")
    p_jobs.add_argument("--finish-window", metavar="HH-HH[,HH-HH]",
                        help="with --times: run counts that end inside these local-time windows, "
                             "e.g. 08-10,20-22")
    p_jobs.add_argument("--tz", metavar="ZONE",
                        help="time zone for --finish-window, --start and --since, e.g. Europe/Helsinki (default UTC; "
                             "--since defaults to the operation file's planning.timezone)")
    p_jobs.add_argument("--start", metavar="HH:MM|ISO",
                        help="with --finish-window: planned install time (default now)")
    p_jobs.add_argument("--max-hours", type=float, default=60, metavar="H",
                        help="with --finish-window: longest job to propose (default 60)")
    p_jobs.add_argument("--json", action="store_true", help="machine-readable output with cache timestamps")
    p_jobs.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the tables")

def _industry(p_industry):
    industry_sub = p_industry.add_subparsers(dest="industry_action", required=True, metavar="ACTION")
    p_status = industry_sub.add_parser("status", help="slots, jobs, idle blueprints, stock, orders and deliveries")
    p_status.add_argument("--corp", action="store_true", help="corporation jobs, blueprints, assets and orders instead of personal holdings")
    p_status.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_status.add_argument("--materials", metavar="FILE", help="UTF-8 file with one extra material type name per line")
    p_status.add_argument("--json", action="store_true", help="stable machine-readable snapshot with ids, quantities and document timestamps")

def _can_build(p_can_build):
    p_can_build.add_argument("--char", help="stored owner character name or id")
    p_can_build.add_argument("--corp", action="store_true",
                             help="use corporation blueprints and assets")
    p_can_build.add_argument("--division", metavar="N|NAME",
                             help="only blueprints in one corporation hangar division")
    p_can_build.add_argument("--site", metavar="NAME",
                             help="a site of the operation file: applies its facility's material bonus "
                                  "(without it, a blueprint sitting in a configured facility gets that "
                                  "facility's bonus); a "
                                  "corp site reads the corporation, a personal site each listed character, "
                                  "and counts jobs possible after trades from the others ('with trades')")
    p_can_build.add_argument("--type", metavar="TEXT",
                             help="case-insensitive blueprint or product name substring")
    p_can_build.add_argument("--builders", metavar="A,B",
                             help="comma-separated stored character names or ids (default: all)")
    p_can_build.add_argument("--json", action="store_true", help="machine-readable output")
    p_can_build.add_argument("--csv", action="store_true", help="CSV rows on stdout")

def _blueprints(p_blueprints):
    p_blueprints.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_blueprints.add_argument("--corp", action="store_true",
                              help="corporation blueprints instead of personal (needs the Director role)")
    p_blueprints.add_argument("--idle", action="store_true",
                              help="exclude blueprints attached to current jobs (needs login --scopes jobs)")
    kind = p_blueprints.add_mutually_exclusive_group()
    kind.add_argument("--copies", action="store_true", help="show blueprint copies only")
    kind.add_argument("--originals", action="store_true", help="show blueprint originals only")
    p_blueprints.add_argument("--division", metavar="N|NAME",
                              help="only one corporation hangar division, by number or player-defined name")
    p_blueprints.add_argument("--type", metavar="TEXT",
                              help="case-insensitive substring of the blueprint type name")
    p_blueprints.add_argument("--group-by", choices=["type", "division"], default="type",
                              help="sort groups by blueprint type (default) or hangar division")
    p_blueprints.add_argument("--items", action="store_true",
                              help="one row per item_id instead of distinct blueprint groups")
    p_blueprints.add_argument("--json", action="store_true", help="machine-readable output")
    p_blueprints.add_argument("--csv", action="store_true", help="CSV rows on stdout")

def _build_cost(p_build_cost):
    p_build_cost.add_argument("type", nargs="+", metavar="TYPE",
                              help="exact type name or numeric id of the thing to build, e.g. Hound; "
                                   "repeat for several items priced off the same order books")
    p_build_cost.add_argument("--runs", type=int, default=1, metavar="N",
                              help="installs in one job (default 1); a blueprint's own maximum runs per "
                                   "install still applies, so ask for more than it and the command says no")
    p_build_cost.add_argument("--me", type=int, default=0, metavar="N",
                              help=f"material efficiency 0..{industry.MAX_ME} (default 0) on the "
                                   f"blueprint being run only; component jobs take --component-me, and "
                                   f"a reaction ignores it entirely")
    p_build_cost.add_argument("--component-me", dest="component_me", type=int, default=None,
                              metavar="N",
                              help=f"material efficiency 0..{industry.MAX_ME} for every component job "
                                   f"(default {industry.DEFAULT_COMPONENT_ME}: component blueprints are "
                                   f"ordinarily owned BPOs researched to the cap, unlike the invented "
                                   f"copy a top blueprint often is); independent of --me, and ignored "
                                   f"by a reaction component")
    p_build_cost.add_argument("--te", type=int, default=0, metavar="N",
                              help=f"time efficiency 0..{industry.MAX_TE} (default 0); changes the job's "
                                   f"time, not its ISK")
    p_build_cost.add_argument("--hub", metavar="HUB",
                              help=f"buy every material at this trade hub's station (default jita): "
                                   f"{', '.join(market.HUBS)}")
    p_build_cost.add_argument("--region", metavar="NAME",
                              help='buy anywhere in this region (exact name or id) instead of at one '
                                   'station; needs --system too, because a region has no single cost index')
    p_build_cost.add_argument("--system", metavar="NAME",
                              help="solar system whose industry cost index bills the install (exact name "
                                   "or id); defaults to the hub's own system - it does not narrow the "
                                   "order book, so --system Amarr with the default scope means buy at "
                                   "Jita, install in Amarr")
    p_build_cost.add_argument("--build", action="append", metavar="TYPE",
                              help="force this material to be built even where buying is cheaper; repeatable")
    p_build_cost.add_argument("--buy", action="append", metavar="TYPE",
                              help="force this material to be bought even where building is cheaper; repeatable")
    p_build_cost.add_argument("--build-all", action="store_true",
                              help="build every direct material that some blueprint makes, wherever that "
                                   "leads - including surplus you did not ask for")
    p_build_cost.add_argument("--buy-all", action="store_true",
                              help="buy every quoted material instead of building it: no component jobs at all")
    p_build_cost.add_argument("--facility", metavar="NAME",
                              help="cost the job in a facility from the operation file (system, tax, "
                                   "material and time bonuses); explicit --facility-tax/"
                                   "--material-multiplier still win. Without --facility, --site or "
                                   "--system the default site's manufacturing facility is used "
                                   "(see: neworks operation show)")
    p_build_cost.add_argument("--site", metavar="NAME",
                              help="like --facility, naming a site of the operation file instead")
    p_build_cost.add_argument("--facility-tax", dest="facility_tax", type=float,
                              default=None, metavar="PCT",
                              help=f"installation tax as a percent of EIV (default "
                                   f"{industry.NPC_STATION_TAX * 100:g} = an NPC station; a player "
                                   f"structure bills differently and only you know its rate)")
    p_build_cost.add_argument("--material-multiplier", dest="material_multiplier", type=float, default=None,
                              metavar="F",
                              help="aggregate material bonus as a fraction of the blueprint's requirements "
                                   "(default 1.0 = none; 0.95 is a 5%% reduction, so quantities and cost fall)")
    p_build_cost.add_argument("--sell-at", dest="sell_at", metavar="HUB",
                              help=f"also price selling the output at this hub's station "
                                   f"({', '.join(market.HUBS)}): list price, net after --seller's fees, "
                                   f"margin per unit, per job and per job-hour, days of regional volume")
    p_build_cost.add_argument("--seller", metavar="CHAR",
                              help="with --sell-at: stored character whose sales tax and broker fee apply "
                                   "and whose own orders are left out of the book")
    p_build_cost.add_argument("--hours-per-run", dest="hours_per_run", type=float, metavar="H",
                              help="with --sell-at: measured hours per run (see jobs --times) for ISK per "
                                   "job-hour, instead of the blueprint's base time")
    p_build_cost.add_argument("--brief", action="store_true",
                              help="compact text: material table, totals, verdict, essential warnings "
                                   "and one scope line; omit explanatory footnotes and request counts")
    p_build_cost.add_argument("--json", action="store_true",
                              help="machine-readable output, including the build option that lost")
    p_build_cost.add_argument("--csv", action="store_true",
                              help="CSV material rows on stdout; the notes go to stderr")

def _restock(p_restock):
    p_restock.add_argument("line", nargs="*", metavar="PRODUCT=UNITS[:ME[:RUNS]][@CHAR]",
                           help="a build line: product units to make, optional blueprint ME (default 0) and "
                                "runs per job (default: the blueprint's limit, or the most an invented copy "
                                "holds); @CHAR names the installer on a personal site")
    p_restock.add_argument("--invent", action="append", metavar="T2PRODUCT=ATTEMPTS[:DECRYPTOR][@CHAR]",
                           help="invention attempts for this T2 product: datacores from the SDE recipe, "
                                "plus one decryptor per attempt when named (e.g. Augmentation); @CHAR names "
                                "the inventor on a personal site; repeatable")
    p_restock.add_argument("--extra", action="append", metavar="TYPE=QTY",
                           help="buy this quantity in full, on top of stock (a buffer); repeatable")
    p_restock.add_argument("--at", metavar="STATION",
                           help="build station (exact name or id, or a structure id): the stock there is "
                                "already on site; optional with --site")
    p_restock.add_argument("--site", metavar="NAME",
                           help="a site of the operation file: its facility is the build location, a corp "
                                "site reads the corporation's hangars, a personal site every listed "
                                "character's own hangars (with transfers between them); default: the "
                                "file's default site when --at is not given")
    p_restock.add_argument("--freight", metavar="ROUTE",
                           help="price the haul as a courier contract on this operation-file route "
                                "(e.g. jita->x-hub); default: the site's freight_in")
    p_restock.add_argument("--hub", default="jita", metavar="HUB",
                           help=f"where to buy and pick up (default jita): {', '.join(market.HUBS)}")
    p_restock.add_argument("--char", help="stored character that reads the corporation's assets")
    p_restock.add_argument("--hauler", metavar="CHAR",
                           help="stored character whose personal hangars also count: at the hub as "
                                "pick-ups, in the build station's system as moves")
    p_restock.add_argument("--cargo", type=float, metavar="M3",
                           help="hold size: inventions and extras first, then lines in the order given, "
                                "the last one cut to what fits")
    p_restock.add_argument("--days", type=float, default=3.0, metavar="N",
                           help="days of builds and inventions the lines cover (default 3): the price "
                                "check scales dip extras from the use per day")
    p_restock.add_argument("--no-price-check", dest="price_check", action="store_false",
                           help="skip the price check: no history or book depth read for the materials")
    p_restock.add_argument("--json", action="store_true", help="machine-readable output")


# The agent skills this plugin ships (`neworks agent install`, MCP prompts).
SKILLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

COMMANDS = (
    Command(("jobs",), help="industry jobs (needs login --scopes jobs)",
            configure=_jobs, handler=jobs.cmd_jobs),
    Command(("industry",), help="compact industry operations snapshot",
            configure=_industry, handler=cmd_industry.cmd_status),
    Command(("can-build",), help="idle blueprint jobs allowed by local stock and builder skills",
            configure=_can_build, handler=cmd_industry.cmd_can_build),
    Command(("blueprints",), help="blueprint originals and copies (needs login --scopes blueprints)",
            configure=_blueprints, handler=cmd_industry.cmd_blueprints),
    Command(("build-cost",), help=("ISK cost of manufacturing an item from its blueprint, priced "
                                   "from live ESI orders (public ESI, no login)"),
            configure=_build_cost, handler=cmd_build_cost.cmd_build_cost),
    Command(("restock",), help=("what planned builds and inventions still need after corp stock, what "
                                "to pick up or buy at a hub, fitted to a cargo hold (needs login "
                                "--scopes assets and the Director role for corp hangars)"),
            configure=_restock, handler=cmd_restock.cmd_restock),
    Command(("production",), help=cmd_production.HELP,
            configure=cmd_production.configure, handler=cmd_production.cmd_production),
    Command(("job-board",), help=("one screen for planning the next jobs at a site: slots, ready and running jobs, "
                                  "idle copies by what they can do, run counts that fit the finish windows, "
                                  "and the inputs per facility (the industry-jobs skill's board)"),
            configure=board.configure, handler=board.run),
)


def plugin() -> Plugin:
    return Plugin(
        name="industry",
        version=__version__,
        api=API_VERSION,
        summary="industry jobs, blueprints, build costs, restock lists and saved production rounds",
        commands=COMMANDS,
        tools=tools.TOOLS,
        skills=SKILLS,
        panels=panels.PANELS,
        alerts=(AlertSource("jobs", collect=alert_source.collect, features=alert_source.features),),
        scopes={feature: list(sso.OPTIONAL_SCOPES[feature]) for feature in FEATURES},
        tables=(SCHEMA,),
        # `production` reads realized costs from the ledger when it is loaded; the ledger requires this
        # plugin, so this soft link never orders or blocks anything.
        enhances=("ledger",),
    )
