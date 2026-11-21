"""The planetary plugin's watch source: finished extractions, and the colony line of a watch frame.

Polled by `skills --watch` and `neworks watch` (`newedenworks.cmd_watch` runs the loop); a new fact,
the same discipline as orders. `--no-colonies` switches it off."""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import esi as esi_mod, render, sso, watchstate
from ..cmd_watch import WatchCycle, WatchSession, warn_once
from . import colonies as cmd_colonies


@dataclass(frozen=True)
class ColonyPoll:
    """One colony poll: what ESI answered, the warnings standing in for what it did not, and whether any
    stored character could be asked at all.

    `asked=False` is a different thing from `eligible=False`: the watch was told to leave colonies alone
    (--no-colonies), so neither a status table nor a consent hint belongs on screen."""
    observations: tuple = ()
    names: dict = field(default_factory=dict)
    warnings: tuple = ()
    eligible: bool = True
    asked: bool = True


def colonies_watch_hint() -> str:
    """What to tell a watcher when nobody can be asked about colonies - and how to fix it."""
    return (f"no stored character has the {cmd_colonies.FEATURE} consent - run: "
            f"neworks login --scopes {cmd_colonies.FEATURE} (pick the character in the browser)")


def colony_snapshot(colony, names: dict[int, str]) -> watchstate.ColonySnapshot:
    """One `cmd_colonies.Colony` as the pure observation watchstate diffs.

    Names are resolved by the caller and handed in: this module is the only place that may talk to ESI,
    and `observe_colonies` has to stay reproducible from a JSON file alone."""
    return watchstate.ColonySnapshot(colony.planet_id, colony.planet_type, colony.solar_system_id,
                                     names.get(colony.solar_system_id), colony.last_update,
                                     tuple(watchstate.ExtractorPin(
                                         pin_id=ex.pin_id, type_id=ex.type_id,
                                         type_name=names.get(ex.type_id),
                                         product_type_id=ex.product_type_id,
                                         product_name=names.get(ex.product_type_id),
                                         qty_per_cycle=ex.qty_per_cycle, cycle_time=ex.cycle_time,
                                         heads=ex.heads, expiry_time=ex.expiry_time)
                                         for ex in colony.layout.extractors))


def colony_names(client: esi_mod.Esi, colonies) -> dict[int, str]:
    """System, extractor and product names for these colonies, resolved once per cycle.

    ESI's colony rows carry ids only. `resolve_names` is disk-cached and leaves out whatever it cannot
    resolve, so a type added this week shows as an id instead of failing the poll - which matters more
    here than in `colonies --detail`: an overnight watch has no chance to ask the user anything, and it
    deliberately does not need an SDE snapshot installed to keep working."""
    ids = set()
    for colony in colonies:
        if colony.solar_system_id:
            ids.add(int(colony.solar_system_id))
        for ex in colony.layout.extractors:
            ids.update(int(ident) for ident in (ex.type_id, ex.product_type_id) if ident)
    return esi_mod.resolve_names(client, ids) if ids else {}


def poll_colony_observations(args, client: esi_mod.Esi, warned: set) -> ColonyPoll:
    """Every character this cycle can be asked for colonies, plus a warning line per one it could not.

    Deliberately quieter than `neworks colonies`, exactly as order polling is: missing consent stops
    all polling and so is said once per run (`colonies_watch_hint`), ESI's refusal of a token that claims
    the scope is remembered for the session because only a browser login changes it, and a transport
    failure is warned about every cycle and retried, because the next one may well work.

    The cost is bounded by ESI's own headers, measured 2026-09-13 against /meta/openapi.json: both colony
    routes publish `x-client-cache-ttl: 600` inside the `char-industry` group (advertised limit 600
    requests per 15 min), and `Esi.get` will not re-request inside that window. At the default 5-minute
    interval every other cycle is therefore answered from the in-process cache, a character with four
    colonies costs at most five conditional GETs per ten minutes, and nothing here polls faster than the
    endpoint's own stated freshness. Nothing here writes either: ESI implements GET only on both routes."""
    wanted = sso.resolve_character(args.char) if getattr(args, "char", None) else None
    warnings, polled, eligible = [], [], False
    for rec in sso.list_characters():
        cid = int(rec["character_id"])
        if wanted is not None and cid != wanted:
            continue
        tok = sso.get_access_token(cid)
        name = tok.get("character_name") or str(cid)
        if not sso.has_feature(tok, cmd_colonies.FEATURE):
            continue
        eligible = True
        try:
            report = cmd_colonies.character_colonies(client, tok, detail=True)
        except cmd_colonies.ColonyAccess as err:
            warnings += warn_once(warned, f"colonies:{cid}", f"{name}: {err}")
            continue
        except (esi_mod.EsiError, RuntimeError) as err:
            warnings.append(f"{name}: {err}")
            continue
        # A colony whose layout could not be read is left out of the observation, so watchstate keeps the
        # pins it stored for that planet untouched: a failed read must never look like an emptied colony.
        # Its warning still appears this cycle, because the next poll may well succeed.
        warnings += list(report.warnings)
        polled.append((cid, name, [colony for colony in report.colonies if colony.layout is not None]))
    names = colony_names(client, [colony for _cid, _name, colonies in polled for colony in colonies])
    observations = tuple(watchstate.ColonyObservation(cid, name,
                                                      tuple(colony_snapshot(c, names) for c in colonies))
                         for cid, name, colonies in polled)
    return ColonyPoll(observations, names, tuple(warnings), eligible)


COLONIES_WATCH_COLUMNS = ["character", "colonies", "extractors", "next extraction ends", "oldest layout"]


def render_colonies_watch_status(observations, names: dict[int, str], now) -> str:
    """The colony line of a `skills --watch` frame: what is being watched and when it wants attention.

    `next extraction ends` is the earliest expiry across every colony of that character - the pin to go
    reprogram first, which is the whole reason to watch planets overnight. `oldest layout` repeats ESI's
    own caveat where a watcher can see it: a colony nobody has opened in the client for a week reports
    pins as of then, so its countdown is only as good as that view."""
    rows, stale = [], False
    for obs in observations:
        pins = [pin for colony in obs.colonies for pin in colony.extractors]
        epochs = [epoch for epoch in (cmd_colonies.epoch_seconds(pin.expiry_time) for pin in pins)
                  if epoch is not None]
        ages = [age for age in (cmd_colonies.age_seconds(colony.last_update, now) for colony in obs.colonies)
                if age is not None]
        if not epochs:
            soonest = "-"
        elif min(epochs) <= now.timestamp():
            soonest = "due"
        else:
            soonest = render.format_duration(min(epochs) - now.timestamp()) + " left"
        oldest = "-" if not ages else (render.format_duration(max(ages)) + " ago"
                                       + (" *" if cmd_colonies.is_stale(max(ages)) else ""))
        stale = stale or cmd_colonies.is_stale(max(ages) if ages else None)
        rows.append([obs.character_name, str(len(obs.colonies)), str(len(pins)), soonest, oldest])
    out = [render.table(COLONIES_WATCH_COLUMNS, rows)] if rows else \
        ["(no colony could be polled this cycle - the warnings above say why)"]
    if stale:
        out.append("* ESI recalculates a colony only when it is opened in the game client; an old layout "
                   "reports pins as of then, not as of now.")
    return "\n".join(out)


def colonies_wanted(args) -> bool:
    return not getattr(args, "no_colonies", False)


def colonies_poll(args, client: esi_mod.Esi, session: WatchSession) -> WatchCycle:
    """One colony turn: claim finished extractions and show when each character's next one ends."""
    colonies = poll_colony_observations(args, client, session.warned)
    warnings = list(colonies.warnings)
    if not colonies.eligible:
        warnings += warn_once(session.warned, "colonies:no-consent", colonies_watch_hint())
    return WatchCycle(warnings=tuple(warnings), colony_observations=colonies.observations,
                      body=render_colonies_watch_status(colonies.observations, colonies.names, client.now()),
                      names=colonies.names)
