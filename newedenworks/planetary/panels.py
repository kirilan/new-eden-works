"""Planetary's dashboard panel: every colony with its extractors' end times (`planetary.colonies`)."""

from __future__ import annotations

from .. import dashboard, universe
from ..panels import PAGE
from ..plugin import Panel

COLONIES = ("colonies", "--detail")


def prefetch(ctx) -> None:
    if ctx.live:
        ctx.start(*COLONIES)


def colonies_data(ctx) -> dict:
    if ctx.live:
        doc, error = ctx.report(*COLONIES)
    else:
        doc, error = None, "live reports off (--no-live)"
    planets = [c.get("planet_id") for ch in (doc or {}).get("characters") or [] for c in ch.get("colonies") or []]
    names = {pid: place.get("name") for pid, place in
             universe.planet_names(ctx.client if ctx.live else None, planets).items() if place.get("name")}
    return {"planetary": {"colonies": dashboard.colonies_section(doc, error, names)}}


PANELS = (Panel("planetary.colonies", "Colonies", PAGE, data=colonies_data, prefetch=prefetch),)
