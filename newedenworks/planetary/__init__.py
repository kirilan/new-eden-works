"""The `planetary` plugin: planetary industry recipes and colony fits (`pi`), live colonies
(`colonies`), the `planets` consent and the holdings-poll tables.

The ledger still takes the holdings polls during its sync and reads them back for `ledger
planetary` and its colony accounting; that link becomes a hook when the ledger moves (step 5).
The colonies watch cycle stays in `cmd_watch` until watch sources arrive (step 7).
"""

from __future__ import annotations

from .. import __version__, market, sso
from ..plugin import API_VERSION, Command, Plugin, Schema, WatchSource
from . import colonies, panels, pi, tools, watch

# This plugin's consent feature (what `login --scopes` accepts), taken from sso.OPTIONAL_SCOPES.
FEATURES = ("planets",)

# The holdings polls behind the colonies' accounting (see the planetary note in ledger_db). Version 1
# is exactly the tables eve-skills schema 5 added; SQLite stores them without IF NOT EXISTS.
PLANETARY_V1 = """
CREATE TABLE IF NOT EXISTS pi_polls (
    ts      TEXT PRIMARY KEY,
    as_of   TEXT NOT NULL,
    sources TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pi_holdings (
    ts      TEXT NOT NULL,
    type_id INTEGER NOT NULL,
    held    INTEGER NOT NULL,
    listed  INTEGER NOT NULL,
    PRIMARY KEY (ts, type_id)
);
"""

PLANETARY_TABLES = ("pi_polls", "pi_holdings")
SCHEMA = Schema("planetary", (PLANETARY_V1,), PLANETARY_TABLES)


# -- argument setup, as moved from the legacy plugin ----------------------------------------------

def _colonies(p_colonies):
    p_colonies.add_argument("--char", help="stored character name or id (default: every stored character)")
    p_colonies.add_argument("--detail", action="store_true",
                            help="read each colony's pins too: extractors with the time their extraction "
                                 "ends, facilities with what they are making, and a count of the rest. "
                                 "With --csv this switches a row from one colony to one extractor")
    p_colonies.add_argument("--json", action="store_true", help="machine-readable output")
    p_colonies.add_argument("--csv", action="store_true", help="CSV rows on stdout instead of the table")


def _pi(p_pi):
    pisub = p_pi.add_subparsers(dest="pi_action", metavar="ACTION")

    p_pi_chain = pisub.add_parser("chain", help="the recipe tree for one product down to its raw materials")
    p_pi_chain.add_argument("type", metavar="PRODUCT",
                            help="exact type name or numeric id of the thing to make, e.g. "
                                 "\"Broadcast Node\"; quantities in the tree are per 1 unit of it")
    p_pi_chain.add_argument("--hub", metavar="HUB",
                            help=f"price every step at this trade hub's station (default jita): "
                                 f"{', '.join(market.HUBS)}")
    p_pi_chain.add_argument("--region", metavar="NAME",
                            help="price every step somewhere in this region (exact name or id) instead of "
                                 "at one station - the whole regional book, so any system's asks win")
    p_pi_chain.add_argument("--customs-rate", dest="customs_rate", type=float, metavar="PCT",
                            help="add a customs column: the percent a corporation sets at its own customs "
                                 "office (0..100). ESI only publishes that rate to the corporation owning "
                                 "the office, so this number is yours to know; each step is billed on the "
                                 "SDE's own per-unit customs values, discounted on inputs and not on output")
    p_pi_chain.add_argument("--json", action="store_true",
                            help="machine-readable tree: every step with its quantity, price and margin")
    p_pi_chain.add_argument("--csv", action="store_true",
                            help="CSV step rows on stdout; the notes go to stderr")

    p_pi_fit = pisub.add_parser("fit", help="a colony layout against its command centre budget")
    p_pi_fit.add_argument("--ccu", type=int, required=True, metavar="N",
                          help=f"command centre upgrade level ({', '.join(str(level) for level in pi.CCU_LEVELS)}) "
                               f"- it is what sets the CPU and powergrid budget everything else has to fit into")
    p_pi_fit.add_argument("--ecu", type=int, default=0, metavar="N",
                          help="extractor control units fitted; each can attach at most "
                               f"{pi.MAX_HEADS_PER_ECU} extractor heads")
    p_pi_fit.add_argument("--heads", type=int, default=None, metavar="N",
                          help="extractor heads to charge for (each draws the ECU's own head CPU/PG); omit "
                               "it and the command reports the most that still fit and which resource binds")
    p_pi_fit.add_argument("--basic", type=int, default=0, metavar="N", help="basic industry facilities")
    p_pi_fit.add_argument("--advanced", type=int, default=0, metavar="N", help="advanced industry facilities")
    p_pi_fit.add_argument("--high-tech", dest="high_tech", type=int, default=0, metavar="N",
                          help="high-tech industry facilities")
    p_pi_fit.add_argument("--storage", type=int, default=0, metavar="N", help="storage facilities")
    p_pi_fit.add_argument("--launchpad", type=int, default=0, metavar="N",
                          help="launchpads - by far the hungriest item on the budget at 3600 CPU / 700 PG")
    p_pi_fit.add_argument("--link-allowance", dest="link_allowance", metavar="CPU,PG",
                          help="charge the layout for keeping inter-planetary links up, as CPU,PG "
                               "(e.g. --link-allowance 500,400); what a colony's links cost depends on "
                               "which links it has and ESI publishes nothing about them, so this is your figure")
    p_pi_fit.add_argument("--json", action="store_true", help="machine-readable load, budget and verdict")

    p_pi_planet = pisub.add_parser("planet-type",
                                   help="one planet type's raw materials and everything it can refine "
                                        "with no imports")
    p_pi_planet.add_argument("planet", metavar="TYPE",
                             help="planet type name or id: Barren, Gas, Ice, Lava, Oceanic, Plasma, "
                                  "Storm, Temperate (or their ids)")
    p_pi_planet.add_argument("--json", action="store_true",
                             help="machine-readable output: raw materials and every reachable product")
    p_pi_planet.add_argument("--csv", action="store_true",
                             help="CSV rows - one per commodity, tier 0 to tier 3 - on stdout")



COMMANDS = (
    Command(("colonies",), help="live planetary colonies of stored characters (read-only ESI; needs login --scopes planets)",
            configure=_colonies, handler=colonies.cmd_colonies),
    Command(("pi",), help=("planetary industry: recipe trees, a colony's CPU/powergrid budget "
                           "and what one planet type can make alone (recipes from local SDE "
                           "data; prices from public ESI, no login)"),
            configure=_pi, handler=pi.cmd_pi),
)


def plugin() -> Plugin:
    return Plugin(
        name="planetary",
        version=__version__,
        api=API_VERSION,
        summary="planetary industry recipes, colony fits and live colonies",
        commands=COMMANDS,
        tools=tools.TOOLS,
        panels=panels.PANELS,
        watch=(WatchSource("colonies", poll=watch.colonies_poll, wanted=watch.colonies_wanted),),
        scopes={feature: list(sso.OPTIONAL_SCOPES[feature]) for feature in FEATURES},
        tables=(SCHEMA,),
    )
