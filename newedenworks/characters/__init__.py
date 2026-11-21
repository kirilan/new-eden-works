"""The `characters` plugin: skills, training queue and clone state, plans, extraction, attributes,
standings, location and implants.

The first area moved out of `legacy`. Its commands return data (`--json` comes from the core) and
render the text they always printed; `skills --watch` hands over to the watch loop.
"""

from __future__ import annotations

from .. import __version__, sso
from ..plugin import API_VERSION, Command, Plugin, WatchSource
from . import skills, tools, views, watch

# This plugin's consent features (what `login --scopes` accepts), taken from sso.OPTIONAL_SCOPES.
FEATURES = ("attributes", "standings", "location", "clones")


# -- argument setup, as moved from the legacy plugin ----------------------------------------------

def _skills(p_skills):
    p_skills.add_argument("--json", action="store_true", help="machine-readable output")
    p_skills.add_argument("--filter", choices=["all", "alpha", "omega"], default="all", help="restrict the trained-skills table")
    p_skills.add_argument("--sort", choices=["name", "level", "sp"], default="name")
    p_skills.add_argument("--trained-only", action="store_true", help="omit the training queue section")
    p_skills.add_argument("--char", help="stored character name or id (default: show every stored character)")
    p_skills.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the table (all stored characters)")
    p_skills.add_argument("--week", action="store_true", help="append SP gained over the last 7 days from local history")
    p_skills.add_argument("--watch", type=int, nargs="?", const=5, metavar="MIN", help="keep refreshing every MIN minutes (default 5), announce finished training; Ctrl-C stops")
    p_skills.add_argument("--notify", action="store_true", help="with --watch: also send notify-send desktop notifications")
    p_skills.add_argument("--full", action="store_true", help="with --watch: keep the full per-character view instead of the compact status table")
    p_skills.add_argument("--ledger", action="store_true",
                          help="with --watch: also run ledger sync, at most once an hour, so the accounting "
                               "ledger never misses ESI's 30-day wallet window")
    p_skills.add_argument("--no-orders", action="store_true",
                          help="with --watch: watch training only; do not poll market orders")
    p_skills.add_argument("--no-colonies", action="store_true",
                          help="with --watch: skip planetary colonies (no extractor-expiry events); needs login --scopes planets")


def _attributes(p_attrs):
    p_attrs.add_argument("--char", help="stored character name or id (default: every stored character)")


def _standings(p_standings):
    p_standings.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_standings.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the tables")


def _travel(p_travel):
    p_travel.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_travel.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the blocks")


def _implants(p_impl):
    p_impl.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_impl.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the blocks")


def _plan(p_plan):
    p_plan.add_argument("--char", help="stored character name or id (required when several are stored)")
    p_plan.add_argument("--rate", type=float, metavar="SPH", help="override SP/hour for every row; by default each skill is priced from the character's attributes per attribute pair (falling back to live training / SP history)")
    p_plan.add_argument("target", nargs="+", metavar="SKILL[:LEVEL]",
                        help='e.g. "Astrogeology:5" (default target L5); missing prerequisites are added automatically')


def _extract(p_extract):
    p_extract.add_argument("--char", help="stored character name or id (required when several are stored)")


COMMANDS = (
    Command(("skills",), help="show skills, queue and clone state (default)", configure=_skills,
            run=skills.skills_data, render=skills.skills_text, csv=skills.skills_csv,
            watch=skills.skills_watch, default=True),
    Command(("summary",), help="one line per character (clone state, SP, queue) plus totals",
            run=skills.summary_data, render=skills.summary_text),
    Command(("attributes",), help="base attributes + remap status", configure=_attributes,
            run=views.attributes_data, render=views.attributes_text),
    Command(("standings",), help="agent / NPC corp / faction standings (needs login --scopes standings)",
            configure=_standings, run=views.standings_data, render=views.standings_text, csv=views.standings_csv),
    Command(("travel",), help="current location, home and jump clones with implants (needs login --scopes location / clones)",
            configure=_travel, run=views.travel_data, render=views.travel_text, csv=views.travel_csv),
    Command(("implants",), help="implants fitted in the active clone (needs login --scopes clones)",
            configure=_implants, run=views.implants_data, render=views.implants_text, csv=views.implants_csv),
    Command(("plan",), help="estimate SP and time to reach target skill levels", configure=_plan,
            run=skills.plan_data, render=skills.plan_text),
    Command(("extract",), help="Skill Extractor math for one character", configure=_extract,
            run=skills.extract_data, render=skills.extract_text),
)


def plugin() -> Plugin:
    return Plugin(
        name="characters",
        version=__version__,
        api=API_VERSION,
        summary="skills, training queue and clone state, plans, extraction, attributes, standings, travel, implants",
        commands=COMMANDS,
        tools=tools.TOOLS,
        watch=(WatchSource("skills", poll=watch.skills_poll),),
        scopes={feature: list(sso.OPTIONAL_SCOPES[feature]) for feature in FEATURES},
    )
