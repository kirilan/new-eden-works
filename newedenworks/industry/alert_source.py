"""Industry's alert source for `alerts run` (`newedenworks.notify`): the ESI reads behind the job alerts.

`collect` reads every character's undelivered jobs, lets `job_alerts` decide the window summary and
the jobs held back for batching, then which jobs are newly within the notice and which announced ones
still wait for delivery. The core decides whether those go out with the reminder; `render` names only
the jobs the message shows. State is `job-alerts.json`, saved after the message was sent.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

from .. import esi as esi_mod, exports, notify, orders, paths, render
from . import industry_status, job_alerts, jobs as jobs_cmd

JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
FEATURE = "jobs"


def features(settings) -> list[tuple[str, str]]:
    return [(FEATURE, JOBS_SCOPE)] if settings.job_alerts else []


def _jobs(client: esi_mod.Esi, chars) -> tuple[list[dict], set[str], list[str]]:
    """Every character's undelivered jobs (the character list covers the jobs it installed for its
    corporation too), the owner keys read, and a warning per character ESI refused. Names are left
    for `_name_jobs`, which only names the few that make it into a message."""
    now = client.now()
    rows, read, failures = [], set(), []
    for tok, public in chars:
        if JOBS_SCOPE not in set(tok.get("scopes") or []):
            continue
        cid = int(tok["character_id"])
        name = public.get("name") or tok.get("character_name") or str(cid)
        try:
            jobs = client.get(f"/characters/{cid}/industry/jobs", token=tok["access_token"])
        except (esi_mod.EsiError, RuntimeError) as err:
            failures.append(f"{name} jobs: {err}")
            continue
        key = orders.owner_key("char", cid)
        read.add(key)
        for job in jobs or []:
            end = render.parse_opt(job.get("end_date"))
            status = jobs_cmd._job_status(job, end, now)
            if status in ("delivered", "cancelled", "reverted"):
                continue
            rows.append({"job_id": job["job_id"], "owner_key": key, "installer": name,
                         "activity": jobs_cmd.ACTIVITY.get(job.get("activity_id"), "industry"),
                         "product_id": jobs_cmd._job_ident(job, "product_type_id", "blueprint_type_id"),
                         "facility_id": jobs_cmd._job_ident(job, *jobs_cmd.FACILITY_FIELDS),
                         "runs": job.get("runs"), "end": job.get("end_date"), "status": status})
    return rows, read, failures


def _name_jobs(client: esi_mod.Esi, jobs: list[dict], chars) -> None:
    """Product and facility names for the jobs in this message."""
    if not jobs:
        return
    ids = {i for j in jobs for i in (j.get("product_id"), j.get("facility_id")) if i}
    names = esi_mod.resolve_names(client, ids)
    # structure_names wants order-like rows: the installer's own token is tried first.
    places = [SimpleNamespace(location_id=j["facility_id"], owner_key=j["owner_key"])
              for j in jobs if j.get("facility_id")]
    names.update(orders.structure_names(client, places, chars))
    for job in jobs:
        job["product"] = exports.name_or_id(names, job.get("product_id"))
        job["location"] = names.get(job.get("facility_id")) or f"facility {job.get('facility_id')}"


def _operation_view() -> tuple[str | None, dict[str, str], list[str]]:
    """(the operation file's finish windows, character name -> site key, warnings). A broken operation
    file costs the window default and the per-site batching, never the job alerts."""
    from .. import operation
    try:
        op = operation.load()
    except Exception as err:            # noqa: BLE001 - reported, not this source's to fix
        return None, {}, [f"operation file not used for job alerts: {err}"]
    sites: dict[str, str] = {}
    for key, site in op.sites.items():
        for name in (*site.characters, *site.readers):
            sites.setdefault(name.casefold(), key)
    return op.planning.finish_windows, sites, []


def _slot_rows(client: esi_mod.Esi, chars, sites: dict[str, str]) -> tuple[list[dict] | None, list[str]]:
    """Slot rows (`industry status`'s own builder) of the operation's characters (everyone without an
    operation file), and a warning when they could not be read (rows None)."""
    wanted = [(tok, pub) for tok, pub in chars if JOBS_SCOPE in set(tok.get("scopes") or [])
              and (not sites or str(pub.get("name") or tok.get("character_name") or "").casefold() in sites)]
    try:
        return industry_status._slots(client, wanted, [], False, False, client.now(), []), []
    except (esi_mod.EsiError, RuntimeError) as err:
        return None, [f"slots not read for the window summary: {err}"]


def load_state() -> dict:
    """job-alerts.json, with the announced jobs taken from the order alerts' state the first time."""
    state = job_alerts.load_state()
    if "announced" not in state:
        try:
            with open(os.path.join(paths.state_dir(), "order-alerts.json"), encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = {}
        state = {**state, "announced": (old.get("jobs") if isinstance(old, dict) else None) or {}}
    return state


def collect(run: notify.AlertRun) -> notify.Batch:
    settings, now, client, chars = run.settings, run.now, run.client, run.chars
    jobs = job_owners = None
    warnings: list[str] = []
    job_failures: list[str] = []
    if settings.job_alerts:
        jobs, job_owners, job_failures = _jobs(client, chars)
        warnings += job_failures
    state = load_state()
    announced_before = {int(k): v for k, v in (state.get("announced") or {}).items()}
    window_state = {k: v for k, v in state.items() if k != "announced"}
    new_window_state, news = window_state, job_alerts.JobNews()
    if jobs is not None and settings.jobs_enabled and not job_failures:
        # A failed jobs read skips this part for the run and keeps its state; the per-job alerts go on.
        planning_windows, sites, problems = _operation_view()
        warnings += problems
        slots = None
        if job_alerts.summary_due(window_state, settings, planning_windows, now):
            slots, problems = _slot_rows(client, chars, sites)
            warnings += problems
        new_window_state, news = job_alerts.evaluate(
            window_state, jobs, slots, settings, now, announced=set(announced_before),
            planning_windows=planning_windows, sites=sites)
    if jobs is None:
        kept, soon, waiting = announced_before, [], []
    else:
        shown = [j for j in jobs if int(j["job_id"]) not in news.hold]
        kept, soon, waiting = job_alerts.evaluate_announced(announced_before, shown, set(job_owners or ()),
                                                            settings, now.timestamp())
    fresh = bool(soon)
    if news.summary:
        soon = []                       # announced now (in the state), listed by the summary instead
    standing = [float(kept[int(j["job_id"])].get("announced_at") or now.timestamp()) for j in waiting]

    def render_parts(reminder: bool) -> notify.Parts:
        shown_waiting = waiting if reminder else []
        summary_jobs = (news.summary["ready"] + news.summary["ending"]) if news.summary else []
        _name_jobs(client, soon + shown_waiting + summary_jobs, chars)
        parts = job_alerts.parts(soon, shown_waiting, news.summary, settings, now)
        if news.hold:
            parts.log_tail = f"; {len(news.hold)} job(s) held back"
        return parts

    def save() -> None:
        job_alerts.save_state({**new_window_state, "announced": {str(k): v for k, v in kept.items()}})

    notes = ([f"held back: {len(news.hold)} job(s) finishing while more of their site ends within "
              f"{settings.batch_minutes:g} min (or quiet hours)"] if news.hold else [])
    return notify.Batch(render=render_parts, save=save, fresh=fresh, standing=standing,
                        warnings=warnings, notes=notes)
