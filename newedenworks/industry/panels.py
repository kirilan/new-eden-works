"""Industry's dashboard panel: slots, finished and running jobs per character (`industry.slots`).

The industry status it reads (one `industry status` per site of the operation file) is shared with
the ledger's stockpile panel, which takes the blueprints from it."""

from __future__ import annotations

from .. import dashboard
from ..panels import PAGE
from ..plugin import Panel

NO_LIVE = "live reports off (--no-live)"


def status_plan() -> list[tuple[tuple[str, ...], set[str] | None]]:
    """The `industry status` reads that cover every site: (arguments, characters kept or None for all).

    Without an operation file this is the corporation snapshot it always was. With one, a corp site is
    read through its first reader and a personal site from the characters' own snapshots, so an
    operation split across a high-sec corporation and a null-sec structure shows on one page."""
    from .. import operation
    op = operation.load()
    if not op.sites:
        return [(("industry", "status", "--corp"), None)]
    plan, personal = [], set()
    for site in op.sites.values():
        if site.owner == "corp":
            args = ("industry", "status", "--corp") + (("--char", site.readers[0]) if site.readers else ())
            if (args, None) not in plan:
                plan.append((args, None))
        else:
            personal.update(name.casefold() for name in site.characters)
    if personal:
        plan.append((("industry", "status"), personal))
    return plan


def merged_status(results) -> tuple[dict | None, str | None]:
    """One status document out of several reads; owners outside a personal site are dropped."""
    owners, warnings, errors = [], [], []
    for (doc, error), keep in results:
        if doc is None:
            errors.append(error)
            continue
        warnings += doc.get("warnings") or []
        for owner in doc.get("owners") or []:
            if keep is None or str(owner.get("owner_name") or "").casefold() in keep:
                owners.append(owner)
    if not owners:
        return None, "; ".join(e for e in errors if e) or "no industry status"
    return {"owners": owners, "warnings": warnings}, ("; ".join(e for e in errors if e) or None)


def prefetch_status(ctx) -> None:
    if ctx.live:
        for args, _keep in ctx.once("industry.status_plan", status_plan):
            ctx.start(*args)


def status(ctx) -> tuple[dict | None, str | None]:
    """The merged industry status of this build, read once whichever panel asks first."""
    def read():
        if not ctx.live:
            return None, NO_LIVE
        plan = ctx.once("industry.status_plan", status_plan)
        return merged_status([(ctx.report(*args), keep) for args, keep in plan])
    return ctx.once("industry.status", read)


def slots_data(ctx) -> dict:
    doc, error = status(ctx)
    return {"jobs": dashboard.jobs_section(doc, error)}


PANELS = (Panel("industry.slots", "Industry slots", PAGE, data=slots_data, prefetch=prefetch_status),)
