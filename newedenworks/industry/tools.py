"""Industry's MCP tools: slots, jobs, blueprints, build costs, restock lists and production rounds."""

from __future__ import annotations

from ..mcp import boolean, flags, integer, number, schema, string, strings
from ..plugin import Tool

SITE = string("site of the operation file (default: its default site)")
CHAR = string("stored character name or id")
CORP = boolean("the corporation's instead of the characters' own")

TOOLS = (
    Tool("job_board", command=("job-board",), title="Job board", json=False,
         description="The industry-jobs skill's one-screen board for a site: slots, ready and running jobs, idle "
                     "copies by what they can do (and where to haul them), measured hours per run with run counts "
                     "that end inside the finish windows, pool and input checks.",
         input_schema=schema(site=SITE, char=string("corp reader on a corp site"),
                             start=string("planned install time HH:MM in the planning zone"),
                             since=string("also list jobs installed since HH:MM or an ISO time"),
                             tz=string("planning time zone"), finish_window=string("e.g. 08-10,20-22"),
                             all_blueprints=boolean("every idle blueprint, also lines with no recent job")),
         args=flags(site="--site", char="--char", start="--start", since="--since", tz="--tz",
                    finish_window="--finish-window", all_blueprints="--all-blueprints")),
    Tool("industry_status", command=("industry", "status"), title="Industry status",
         description="One snapshot of slots, jobs (ready, running), idle blueprints with their activities "
                     "and what they invent, and stock, per owner. Start here for any industry question.",
         input_schema=schema(corp=CORP, char=CHAR), args=flags(corp="--corp", char="--char")),
    Tool("industry_jobs", command=("jobs",), title="Industry jobs",
         description="Industry jobs: per character, or slots per character (slots), grouped (group), installed "
                     "since a time (since, delivered included), or measured hours per run (times, with "
                     "finish_window and tz for window-fitting run counts).",
         input_schema=schema(char=CHAR, corp=CORP, slots=boolean("slots used, ready and free per character"),
                             group=boolean("group identical jobs"), completed=boolean("include delivered jobs"),
                             since=string("only jobs installed since HH:MM or an ISO time"),
                             times=boolean("measured hours per run and window-fitting run counts"),
                             site=SITE, days=integer("history window in days for times", 1),
                             finish_window=string("hours a job should end in, e.g. 08-10,20-22"),
                             tz=string("time zone, e.g. Europe/London")),
         args=flags(char="--char", corp="--corp", slots="--slots", group="--group", completed="--completed",
                    since="--since", times="--times", site="--site", days="--days",
                    finish_window="--finish-window", tz="--tz")),
    Tool("blueprints", command=("blueprints",), title="Blueprints",
         description="Blueprints held, with runs, ME/TE and location; idle ones only with idle.",
         input_schema=schema(char=CHAR, corp=CORP, idle=boolean("only blueprints not in a job"),
                             copies=boolean("copies only"), originals=boolean("originals only"),
                             type=string("name contains this text")),
         args=flags(char="--char", corp="--corp", idle="--idle", copies="--copies", originals="--originals",
                    type="--type")),
    Tool("can_build", command=("can-build",), title="Can build",
         description="Jobs the stock and the builders' skills allow right now, per blueprint.",
         input_schema=schema(char=CHAR, corp=CORP, site=SITE, type=string("name contains this text"),
                             builders=string("comma-separated character names")),
         args=flags(char="--char", corp="--corp", site="--site", type="--type", builders="--builders")),
    Tool("build_cost", command=("build-cost",), title="Build cost",
         description="What building items costs now (materials at hub prices, job fees), buy or build per "
                     "component; with sell_at and seller the margin per unit and per job-hour.",
         input_schema=schema(required=("items",), items=strings("exact type names or ids to build"),
                             runs=integer("runs per job", 1), me=integer("material efficiency of the blueprint", 0),
                             site=SITE, hub=string("hub for material prices, e.g. jita"),
                             sell_at=string("hub to price the output at"), seller=string("seller character (tax and fees)"),
                             brief=boolean("compact buy/build decision")),
         args=flags(runs="--runs", me="--me", site="--site", hub="--hub", sell_at="--sell-at",
                    seller="--seller", brief="--brief", items=None)),
    Tool("restock", command=("restock",), title="Restock list",
         description="What the next builds and inventions still need, netted against stock, fitted to cargo, "
                     "with prices checked against their normal: the shopping list.",
         input_schema=schema(products=strings("PRODUCT=UNITS[:ME[:RUNS]][@CHAR] lines"), site=SITE,
                             invent=strings("T2PRODUCT=ATTEMPTS[:DECRYPTOR][@CHAR] lines"),
                             extra=strings("TYPE=QTY lines to add"), hub=string("hub to buy at"),
                             hauler=string("character hauling"), cargo=number("cargo in m3"),
                             days=integer("days of building to cover", 1)),
         args=flags(site="--site", invent="--invent", extra="--extra", hub="--hub", hauler="--hauler",
                    cargo="--cargo", days="--days", products=None)),
    Tool("production_round_list", command=("production", "round"), title="Production rounds",
         description="Saved production rounds of a site (the agreed job checklists).",
         input_schema=schema(site=SITE), args=flags("list", site="--site")),
    Tool("production_round_check", command=("production", "round"), title="Check a production round",
         description="An active round against live jobs: installed, changed, remaining, blocked, awaiting refresh.",
         input_schema=schema(site=SITE, id=integer("round id (default: the active one)", 1)),
         args=flags("check", site="--site", id=None)),
    Tool("production_supply", command=("production", "supply"), title="Production supply",
         description="Input runway: depletion per input, the first blocked line, purchase deadlines and "
                     "depth-priced purchases.",
         input_schema=schema(site=SITE, days=integer("days to plan", 1), lead_days=integer("days a purchase takes", 0),
                             hub=string("hub to buy at")),
         args=flags(site="--site", days="--days", lead_days="--lead-days", hub="--hub")),
    Tool("production_replenish", command=("production", "replenish"), title="Production replenish",
         description="Sold-out or low listings against held, listed and incoming output, per hub.",
         input_schema=schema(site=SITE, hub=string("hub key, e.g. jita"), days=integer("days of sales", 1)),
         args=flags(site="--site", hub="--hub", days="--days")),
    Tool("production_round_save", command=("production", "round"), title="Save a production round",
         read_only=False, idempotent=False,
         description="Save an agreed round from a JSON file (see docs/production.md).",
         input_schema=schema(required=("file",), file=string("path of the round file"), site=SITE),
         args=flags("save", site="--site", file=None)),
    Tool("production_round_handoff", command=("production", "round"), title="Record a handoff",
         read_only=False, idempotent=False,
         description="Record that the stack of blueprints and materials passed to the next holder "
                     "(only after the user confirmed it).",
         input_schema=schema(required=("holder", "facility"), holder=string("character now holding it"),
                             facility=string("facility it sits in"), site=SITE, id=integer("round id", 1),
                             hangar=string("hangar", ("Hangar", "Deliveries")), note=string("note")),
         args=flags("handoff", site="--site", holder="--holder", facility="--facility", hangar="--hangar",
                    note="--note", id=None)),
)
