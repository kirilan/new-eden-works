"""The `jobs` command: industry jobs per character and corporation, slots, measured times and what was
installed since a moment (moved out of exports with the industry plugin)."""


from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone

from .. import esi as esi_mod, freshness, render, sso
from .. import exports


# CCP's industry activity codes, as the SDE's `industryActivities` numbers them - ESI sends the
# number, never a name. 2 and 6 were never used and 7 (reverse engineering) went away with T3
# invention, so a live job carries one of these six; any other code stays visible as itself rather
# than being guessed at.
ACTIVITY = {1: "manufacturing", 3: "time efficiency research", 4: "material efficiency research",
            5: "copying", 8: "invention", 9: "reaction"}

# Where the job is installed, best-named id first. `facility_id` is on every job from both
# endpoints; `station_id` is the character endpoint's NPC-station spelling and `location_id` the
# corporation endpoint's. `blueprint_location_id` is deliberately absent: it is a hangar division
# inside the facility, not the facility.
FACILITY_FIELDS = ("facility_id", "station_id", "location_id")

JOB_CSV_COLUMNS = ["character", "status", "activity", "product", "runs", "time",
                   "installed_in", "installer", "start_date", "end_date", "duration_h",
                   "hours_per_run"]

# Slot formulas published by CCP's SDE skill catalogue: every category starts with one slot,
# and the basic and advanced skills add one each per active level.
SLOT_SKILLS = {
    "manufacturing": (3387, 24625),       # Mass Production, Advanced Mass Production
    "science": (3406, 24624),             # Laboratory Operation, Advanced Laboratory Operation
    "reaction": (45748, 45749),           # Mass Reactions, Advanced Mass Reactions
}
SLOT_ACTIVITY = {1: "manufacturing", 3: "science", 4: "science", 5: "science",
                 8: "science", 9: "reaction"}


def _job_ident(job: dict, *fields: str) -> int | None:
    """The first of `fields` carrying a real id, or None.

    ESI writes an id it has nothing to say about as 0 about as often as it omits the key, and
    `id 0` names nothing, so both spellings of absent are treated the same way."""
    for name in fields:
        ident = exports._ident(job.get(name))
        if ident:
            return ident
    return None


def _job_status(job: dict, end, now) -> str:
    """What the job is actually doing, which is not always what `status` says.

    ESI leaves a finished job `active` until somebody delivers it - its own `ready` value is one
    live jobs are not seen to carry - and that difference is most of what this view is for:
    `ready` means the thing is built and waiting to be collected. A paused job (its structure
    dropped out of power, say) keeps the `end_date` it had when the clock stopped, so it is
    neither running nor ready and must not be read as either."""
    status = job.get("status") or "?"
    if status != "active":
        return status
    if job.get("pause_date"):
        return "paused"
    return "ready" if end is not None and end <= now else "active"


def _runs_cell(job: dict) -> str:
    """The job's run count, plus what one run is worth where that is a second number.

    `runs` is how many times the job runs: units manufactured, copies cut, or invention attempts.
    ESI describes `licensed_runs` only as the number of runs the blueprint is licensed for; that is
    sufficient to render copy jobs, but not precise enough to reinterpret invention jobs. A
    finished invention reports `successful_runs`, so that leads when present: `3/10` is three
    successes out of ten attempts.
    """
    runs = exports._ident(job.get("runs"))
    if runs is None:
        return "-"
    done = exports._ident(job.get("successful_runs"))
    if done is not None:
        return f"{done}/{runs}"
    licensed = exports._ident(job.get("licensed_runs"))
    return f"{runs} x{licensed}" if licensed else str(runs)


def _job_rows(jobs, now):
    """Normalized job rows; names are resolved separately in one batched ESI call."""
    rows, ids = [], set()
    for job in jobs:
        product = _job_ident(job, "product_type_id", "blueprint_type_id")
        facility = _job_ident(job, *FACILITY_FIELDS)
        installer = _job_ident(job, "installer_id")
        ids.update(ident for ident in (product, facility, installer) if ident is not None)
        start = render.parse_opt(job.get("start_date"))
        end = render.parse_opt(job.get("end_date"))
        status = _job_status(job, end, now)
        if status == "paused":
            when = "-"
        elif status == "active" and end:
            when = f"{render.format_duration((end - now).total_seconds())} left"
        elif end:
            when = end.strftime("%b %d %H:%M")
        else:
            when = "-"
        duration_h = ((end - start).total_seconds() / 3600.0
                      if start is not None and end is not None else None)
        runs = exports._ident(job.get("runs"))
        activity_id = exports._ident(job.get("activity_id"))
        rows.append({
            "job_id": _job_ident(job, "job_id"),
            "status": status,
            "activity_id": activity_id,
            "activity": ACTIVITY.get(activity_id, f"activity {job.get('activity_id')}"),
            "product_id": product,
            "product": None,
            "runs": _runs_cell(job),
            "run_count": runs,
            "blueprint_id": _job_ident(job, "blueprint_id"),
            "time": when,
            "facility_id": facility,
            "installed_in": None,
            "installer_id": installer,
            "installer": None,
            "start_date": job.get("start_date"),
            "end_date": job.get("end_date"),
            "start": start,
            "end": end,
            "duration_h": duration_h,
            "hours_per_run": duration_h / runs if duration_h is not None and runs else None,
        })
    rows.sort(key=lambda row: row["end"] or render.parse_ts("9999-01-01T00:00:00+00:00"))
    return rows, ids


def _resolve_job_rows(client, rows, ids) -> None:
    names = esi_mod.resolve_names(client, ids) if ids else {}
    for row in rows:
        row["product"] = exports.name_or_id(names, row["product_id"])
        row["installed_in"] = exports.name_or_id(names, row["facility_id"])
        row["installer"] = exports.name_or_id(names, row["installer_id"])


def _job_csv_row(owner: str, row: dict) -> list:
    return [owner, row["status"], row["activity"], row["product"], row["runs"], row["time"],
            row["installed_in"], row["installer"], row["start_date"] or "", row["end_date"] or "",
            "" if row["duration_h"] is None else f"{row['duration_h']:.6f}".rstrip("0").rstrip("."),
            "" if row["hours_per_run"] is None else
            f"{row['hours_per_run']:.6f}".rstrip("0").rstrip(".")]


def _end_range(rows: list[dict], now) -> str:
    ends = sorted(row["end"] for row in rows if row["end"] is not None)
    if not ends:
        return "-"
    active = rows[0]["status"] == "active"
    if active:
        cells = [render.format_duration((stamp - now).total_seconds()) for stamp in ends]
        return f"{cells[0]} left" if cells[0] == cells[-1] else f"{cells[0]}–{cells[-1]} left"
    first, last = ends[0], ends[-1]
    if first == last:
        return first.strftime("%b %d %H:%M")
    if first.date() == last.date():
        return f"{first.strftime('%b %d %H:%M')}–{last.strftime('%H:%M')}"
    return f"{first.strftime('%b %d %H:%M')}–{last.strftime('%b %d %H:%M')}"


def _group_job_rows(rows: list[dict], now) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["installer"], row["status"], row["activity"], row["product"], row["runs"])
        groups.setdefault(key, []).append(row)
    out = []
    for key, members in groups.items():
        installer, status, activity, product, runs = key
        out.append({"installer": installer, "status": status, "activity": activity,
                    "product": product, "runs": runs, "count": len(members),
                    "time": _end_range(members, now),
                    "_end": min((row["end"] for row in members if row["end"] is not None),
                                default=None)})
    far = render.parse_ts("9999-01-01T00:00:00+00:00")
    return sorted(out, key=lambda row: ((row["installer"] or "").casefold(), row["_end"] or far,
                                       row["product"].casefold()))


def _jobs_path(character_id: int, corporation_id: int | None, completed: bool) -> str:
    path = (f"/corporations/{corporation_id}/industry/jobs" if corporation_id is not None
            else f"/characters/{character_id}/industry/jobs")
    return path + ("?include_completed=true" if completed else "")


def _fetch_jobs_meta(client, tok, corporation_id: int | None, completed: bool):
    path = _jobs_path(int(tok["character_id"]), corporation_id, completed)
    return (client.get_all_meta(path, token=tok["access_token"]) if corporation_id is not None
            else client.get_meta(path, token=tok["access_token"]))


def _fetch_jobs(client, tok, corporation_id: int | None, completed: bool):
    return _fetch_jobs_meta(client, tok, corporation_id, completed)[0]


def _slot_cell(used: int, maximum: int | None, ready: int) -> str:
    free = None if maximum is None else max(maximum - used, 0)
    budget = f"{used} / ? / ?" if maximum is None else f"{used} / {maximum} / {free}"
    return f"{budget} ({ready} ready)"


def _cmd_job_slots(args):
    records = sso.list_characters()
    if not records:
        raise RuntimeError("not logged in - run: neworks login")
    wanted = sso.resolve_character(args.char) if args.char else None
    client = esi_mod.Esi(esi_mod.default_user_agent(sso.load_config()))
    now = client.now()
    contexts, hints, failures = [], [], []
    for rec in records:
        cid = int(rec["character_id"])
        if wanted is not None and cid != wanted:
            continue
        tok = sso.get_access_token(cid)
        name = tok.get("character_name") or str(cid)
        granted = set(tok.get("scopes") or [])
        if not ({sso.OPTIONAL_SCOPES["jobs"][0], sso.OPTIONAL_SCOPES["jobs"][1]} & granted):
            hints.append(exports.hint(name, "jobs"))
            continue
        public = client.get(f"/characters/{cid}")
        contexts.append({"token": tok, "public": public, "name": public.get("name") or name,
                         "jobs": []})

    corp_jobs: dict[int, list] = {}
    for corp_id in dict.fromkeys(exports.corp_of(ctx["public"]) for ctx in contexts):
        if corp_id is None:
            continue
        candidates = [ctx for ctx in contexts
                      if exports.corp_of(ctx["public"]) == corp_id and
                      sso.OPTIONAL_SCOPES["jobs"][1] in set(ctx["token"].get("scopes") or [])]
        for ctx in candidates:
            try:
                corp_jobs[corp_id] = list(_fetch_jobs(client, ctx["token"], corp_id, False))
                break
            except esi_mod.AuthError:
                continue

    for ctx in contexts:
        tok = ctx["token"]
        cid = int(tok["character_id"])
        granted = set(tok.get("scopes") or [])
        combined = []
        if sso.OPTIONAL_SCOPES["jobs"][0] in granted:
            try:
                combined.extend(_fetch_jobs(client, tok, None, False))
            except esi_mod.AuthError as err:
                failures.append(f"{ctx['name']}: personal jobs refused ({err})")
        combined.extend(job for job in corp_jobs.get(exports.corp_of(ctx["public"]), [])
                        if _job_ident(job, "installer_id") == cid)
        deduped = {}
        for job in combined:
            ident = _job_ident(job, "job_id")
            deduped[("id", ident) if ident is not None else ("row", id(job))] = job
        ctx["jobs"] = list(deduped.values())
        ctx["skills"] = None
        if "esi-skills.read_skills.v1" in granted:
            try:
                doc = client.get(f"/characters/{cid}/skills", token=tok["access_token"])
                ctx["skills"] = {int(row["skill_id"]): int(row.get("active_skill_level",
                                                                  row.get("trained_skill_level", 0)))
                                 for row in doc.get("skills", [])}
            except esi_mod.AuthError:
                pass

    result = []
    for ctx in contexts:
        counts = {kind: {"used": 0, "ready": 0, "ends": []} for kind in SLOT_SKILLS}
        for job in ctx["jobs"]:
            activity = _job_ident(job, "activity_id")
            kind = SLOT_ACTIVITY.get(activity)
            if kind is None:
                continue
            end = render.parse_opt(job.get("end_date"))
            status = _job_status(job, end, now)
            if status in {"delivered", "cancelled"}:
                continue
            counts[kind]["used"] += 1
            if status == "ready":
                counts[kind]["ready"] += 1
            elif status == "active" and end is not None:
                counts[kind]["ends"].append(end)
        maxima = {kind: (None if ctx["skills"] is None else
                         1 + sum(ctx["skills"].get(skill_id, 0) for skill_id in skill_ids))
                  for kind, skill_ids in SLOT_SKILLS.items()}
        next_end = min((end for cell in counts.values() for end in cell["ends"]), default=None)
        result.append({"name": ctx["name"], "counts": counts, "maxima": maxima,
                       "next": None if next_end is None else
                       f"{render.format_duration((next_end - now).total_seconds())} left"})

    for line in failures:
        print(f"warning: {line}", file=sys.stderr)
    if args.csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["character", "manufacturing_used", "manufacturing_max", "manufacturing_free",
                         "manufacturing_ready", "science_used", "science_max", "science_free",
                         "science_ready", "reaction_used", "reaction_max", "reaction_free",
                         "reaction_ready", "next_end"])
        for item in result:
            row = [item["name"]]
            for kind in SLOT_SKILLS:
                used, maximum = item["counts"][kind]["used"], item["maxima"][kind]
                row.extend([used, "" if maximum is None else maximum,
                            "" if maximum is None else max(maximum - used, 0),
                            item["counts"][kind]["ready"]])
            row.append(item["next"] or "")
            writer.writerow(row)
        for line in hints:
            print(line, file=sys.stderr)
        return
    rows = [[item["name"],
             _slot_cell(item["counts"]["manufacturing"]["used"], item["maxima"]["manufacturing"],
                        item["counts"]["manufacturing"]["ready"]),
             _slot_cell(item["counts"]["science"]["used"], item["maxima"]["science"],
                        item["counts"]["science"]["ready"]),
             _slot_cell(item["counts"]["reaction"]["used"], item["maxima"]["reaction"],
                        item["counts"]["reaction"]["ready"]),
             item["next"] or "-"] for item in result]
    blocks = list(hints)
    blocks.append(render.table(["character", "manufacturing used / max / free", "science used / max / free",
                                "reaction used / max / free", "next running end"], rows)
                  if rows else "(no readable jobs)")
    print("\n\n".join(blocks))


def _cmd_job_times(args, client, all_rows, hints, failures) -> None:
    """`jobs --times`: measured hours per run, optionally with window-fitting run counts."""
    from . import job_times
    now = client.now()
    tz = job_times.zone(args.tz)
    windows = job_times.parse_windows(args.finish_window) if args.finish_window else None
    start = job_times.parse_start(args.start, tz, now) if windows else None
    since = now - timedelta(days=args.days)
    from .. import operation
    op = operation.load()
    facility_names = {f.location_id: name for name, f in op.facilities.items() if f.location_id}
    site = op.site(args.site) if getattr(args, "site", None) else None
    only = None
    if getattr(args, "facility", None):
        chosen = op.facility(args.facility)
        if chosen.location_id is None:
            raise RuntimeError(f"facility {chosen.name} has no location_id in the operation file")
        only = chosen.location_id
    documents = []
    unscaled: list[dict] = []
    for owner, rows, meta, corp_id in all_rows:
        times = job_times.median_times(rows, since, facility_names, only)
        if site is not None:
            times, missing = job_times.site_times(times, op, site)
            unscaled.extend({"owner": owner, **row} for row in missing)
        if windows:
            for row in times:
                row["fits"] = job_times.window_fits(start, row["hours_per_run"], windows, tz,
                                                    max_hours=args.max_hours)
        documents.append((owner, meta, corp_id, times))
    if args.json:
        print(json.dumps({
            "days": args.days, "time_zone": args.tz or "UTC", "site": site.name if site else None,
            "facilities": ({activity: site.facility_for(activity) for activity in operation.ACTIVITIES}
                           if site else None),
            "unscaled": unscaled if site else None,
            "finish_window": args.finish_window, "start": start.isoformat().replace("+00:00", "Z") if start else None,
            "max_hours": args.max_hours if windows else None, "hints": hints, "warnings": failures,
            "documents": [{"owner": owner, "corporation_id": corp_id,
                           "cache": freshness.document(meta) if corp_id is not None else None,
                           "times": times} for owner, meta, corp_id, times in documents]}, indent=2))
        return

    def local(stamp: str) -> str:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(tz)
        return moment.strftime("%a %H:%M")

    blocks = list(hints)
    for owner, meta, corp_id, times in documents:
        headers = ["activity", "product", "installer", "facility", "h/run", "range", "jobs"]
        if windows:
            headers.append("runs -> end")
        table_rows = []
        for row in times:
            mark = "~" if row.get("scaled_from") else ""
            cells = [row["activity"], row["product"], row["installer"], row["facility"],
                     f"{mark}{row['hours_per_run']:.2f}",
                     f"{row['min_hours']:.2f}-{row['max_hours']:.2f}", str(row["jobs"])]
            if windows:
                fits = row["fits"][:2]
                cells.append(" | ".join(f"{f['runs']} -> {local(f['end'])}" for f in fits) or "no fit")
            table_rows.append(cells)
        table = render.table(headers, table_rows) if table_rows else f"(no timed jobs in the last {args.days} days)"
        cache = f"\n{freshness.line('corp jobs', meta)}" if corp_id is not None else ""
        blocks.append(f"{owner}{cache}\n{table}")
    if site is not None:
        where = ", ".join(sorted({f"{a}: {site.facility_for(a)}" for a in ("manufacturing", "invention", "copying")}))
        note = (f"site {site.name} ({where}): times are where the site does each activity; ~ = measured at "
                "another facility of the operation file and converted by the two facilities' time multipliers")
        if unscaled:
            seen = sorted({(u["activity"], u["product"], u["installer"], u.get("facility") or str(u.get("facility_id")))
                           for u in unscaled})
            note += ("\nno time (measured only at a facility the operation file does not know, so not scaled): "
                     + "; ".join(f"{a} {p} by {i} at {f}" for a, p, i, f in seen))
        blocks.append(note)
    if windows:
        blocks.append(f"runs -> end: the most runs ending in each {args.finish_window} window ({args.tz or 'UTC'}) "
                      f"when installed {local(start.isoformat())}, longest first, at most {args.max_hours:g} h; "
                      "cap by the runs left on the blueprint. h/run is the median over the last "
                      f"{args.days} days (per attempt for invention).")
    print("\n\n".join(blocks))


def _utc_iso(moment) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _since_groups(rows: list[dict], facility_names: dict[int, str]) -> list[dict]:
    """`jobs --since` rows grouped by installer, activity, facility, product and runs."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["installer_id"], row["activity"], row["facility_id"], row["product_id"], row["run_count"])
        groups.setdefault(key, []).append(row)
    out = []
    for members in groups.values():
        first = members[0]
        statuses: dict[str, int] = {}
        for row in members:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        status = (next(iter(statuses)) if len(statuses) == 1 else
                  ", ".join(f"{count} {name}" for name, count in sorted(statuses.items())))
        starts = [row["start"] for row in members if row["start"] is not None]
        ends = [row["end"] for row in members if row["end"] is not None]
        out.append({
            "installer": first["installer"], "installer_id": first["installer_id"],
            "activity": first["activity"], "product": first["product"], "product_id": first["product_id"],
            "runs": first["run_count"], "count": len(members), "facility_id": first["facility_id"],
            "facility": facility_names.get(first["facility_id"]) or first["installed_in"],
            "blueprint_ids": sorted({row["blueprint_id"] for row in members if row["blueprint_id"] is not None}),
            "start": _utc_iso(min(starts)) if starts else None,
            "end": _utc_iso(max(ends)) if ends else None,
            "status": status,
            "_start": min(starts) if starts else None,
        })
    out.sort(key=lambda row: (row["_start"] is None, row["_start"] or 0, (row["installer"] or "").casefold(),
                              (row["product"] or "").casefold()))
    for row in out:
        del row["_start"]
    return out


def _cmd_jobs_since(args, client, all_rows, hints, failures, since, tz, zone_name, site) -> None:
    """`jobs --since WHEN`: what was installed since a moment ("did I miss anything")."""
    from .. import operation
    op = operation.load()
    facility_names = {f.location_id: name for name, f in op.facilities.items() if f.location_id}
    documents = []
    for owner, rows, meta, corp_id in all_rows:
        recent = [row for row in rows if row["start"] is not None and row["start"] >= since]
        documents.append((owner, meta, corp_id, _since_groups(recent, facility_names)))
    if args.json:
        print(json.dumps({
            "since": _utc_iso(since), "time_zone": zone_name, "site": site.name if site else None,
            "hints": hints, "warnings": failures,
            "documents": [{"owner": owner, "corporation_id": corp_id, "cache": freshness.document(meta),
                           "jobs": groups} for owner, meta, corp_id, groups in documents]}, indent=2))
        return

    def local(stamp: str | None) -> str:
        if not stamp:
            return "-"
        return render.parse_ts(stamp).astimezone(tz).strftime("%b %d %H:%M")

    blocks = list(hints)
    blocks.append(f"jobs installed since {since.astimezone(tz).strftime('%a %b %d %H:%M')} {zone_name}"
                  + (f" (site {site.name})" if site else "") + "; times in " + zone_name)
    for owner, meta, _corp_id, groups in documents:
        table_rows = [[row["installer"], row["activity"], row["product"], str(row["runs"]), str(row["count"]),
                       row["facility"], local(row["start"]), local(row["end"]), row["status"]] for row in groups]
        table = (render.table(["installer", "activity", "product", "runs", "count", "facility", "first start",
                               "last end", "status"], table_rows)
                 if table_rows else "(no jobs installed since then)")
        blocks.append(f"{owner}\n{freshness.line('jobs', meta)}\n{table}")
    print("\n\n".join(blocks))


def cmd_jobs(args):
    if getattr(args, "slots", False):
        if getattr(args, "since", None):
            raise RuntimeError("--since cannot be combined with --slots")
        return _cmd_job_slots(args)
    times = getattr(args, "times", False)
    since_spec = getattr(args, "since", None)
    if since_spec:
        if times:
            raise RuntimeError("--since cannot be combined with --times")
        if args.group or args.csv:
            raise RuntimeError("--since groups its own rows and has --json; it cannot be combined with "
                               "--group or --csv")
        if any(getattr(args, flag, None) for flag in ("finish_window", "start", "facility")):
            raise RuntimeError("--finish-window, --start and --facility only apply with --times")
    if times and (args.group or args.csv):
        raise RuntimeError("--times has its own table and --json; it cannot be combined with --group or --csv")
    if not times and not since_spec and any(getattr(args, flag, None)
                                            for flag in ("finish_window", "start", "tz", "facility", "site")):
        raise RuntimeError("--finish-window, --start, --tz, --facility and --site only apply with --times "
                           "(--tz and --site also with --since)")
    site = None
    if getattr(args, "site", None):
        if getattr(args, "facility", None):
            raise RuntimeError("--site and --facility cannot be combined: --site already picks the "
                               "facility of each activity")
        from .. import operation
        site = operation.load().site(args.site)
        if site.owner == "corp":
            args.corp = True                      # a corp site's jobs are the corporation's
            if not args.char and site.readers:
                args.char = site.readers[0]
    since = tz = zone_name = None
    if since_spec:
        from . import job_times
        from .. import operation
        zone_name = args.tz or operation.load().planning.timezone or "UTC"
        tz = job_times.zone(zone_name)
    completed = args.completed or times or bool(since_spec)
    scope = ("esi-industry.read_corporation_jobs.v1" if args.corp
             else "esi-industry.read_character_jobs.v1")
    client, chars, hints = exports.targets(args, [("jobs", scope)])
    if site is not None and site.owner == "personal" and not args.char:
        members = {name.casefold() for name in site.characters}
        chars = [(tok, public) for tok, public in chars
                 if (tok.get("character_name") or public.get("name") or "").casefold() in members]
    now = client.now()
    if since_spec:
        since = job_times.parse_since(since_spec, tz, now)
    blocks, failures, all_rows = list(hints), [], []

    owners: list[tuple[str, dict, dict, int | None]] = []
    if args.corp:
        by_corp: dict[int, list[tuple[dict, dict]]] = {}
        for tok, public in chars:
            corp_id = exports.corp_of(public)
            if corp_id is None:
                failures.append(f"{public.get('name') or tok['character_id']}: no corporation id on the public record")
                continue
            by_corp.setdefault(corp_id, []).append((tok, public))
        for corp_id, candidates in by_corp.items():
            refused = []
            for tok, public in candidates:
                cname = public.get("name") or str(tok["character_id"])
                try:
                    jobs, meta = _fetch_jobs_meta(client, tok, corp_id, completed)
                except esi_mod.AuthError as err:
                    refused.append(f"{cname}: {err}")
                    continue
                owners.append((f"Corporation {corp_id} (read by {cname})",
                               {"jobs": jobs, "meta": meta}, tok, corp_id))
                break
            else:
                failures.append("; ".join(refused) +
                                " - corporation endpoints need the matching director/Account-Manager role")
    else:
        for tok, public in chars:
            cname = public.get("name") or str(tok["character_id"])
            try:
                jobs, meta = _fetch_jobs_meta(client, tok, None, completed)
            except esi_mod.AuthError as err:
                failures.append(f"{cname}: ESI refused ({err}) - the jobs consent may no longer be granted")
                continue
            owners.append((cname, {"jobs": jobs, "meta": meta}, tok, None))

    for owner, payload, _tok, corp_id in owners:
        rows, ids = _job_rows(payload["jobs"], now)
        _resolve_job_rows(client, rows, ids)
        all_rows.append((owner, rows, payload["meta"], corp_id))
    for line in failures:
        print(f"warning: {line}", file=sys.stderr)
    if times:
        return _cmd_job_times(args, client, all_rows, hints, failures)
    if since_spec:
        return _cmd_jobs_since(args, client, all_rows, hints, failures, since, tz, zone_name, site)
    if getattr(args, "json", False):
        documents = []
        for owner, rows, meta, corp_id in all_rows:
            selected = _group_job_rows(rows, now) if args.group else rows
            if args.group:
                payload_rows = [{key: row[key] for key in
                                 ("installer", "status", "activity", "product", "runs", "count", "time")}
                                for row in selected]
            else:
                payload_rows = [{key: row[key] for key in
                                 ("job_id", "status", "activity_id", "activity", "product_id",
                                  "product", "runs", "time", "facility_id", "installed_in",
                                  "installer_id", "installer", "start_date", "end_date",
                                  "duration_h", "hours_per_run")}
                                for row in selected]
            documents.append({"owner": owner, "corporation_id": corp_id,
                              "cache": freshness.document(meta) if corp_id is not None else None,
                              "jobs": payload_rows})
        print(json.dumps({"grouped": bool(args.group), "hints": hints, "warnings": failures,
                          "documents": documents}, indent=2))
        return
    if args.csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        if args.group:
            writer.writerow(["installer", "status", "activity", "product", "runs", "count", "time"])
            for _owner, rows, _meta, _corp_id in all_rows:
                for row in _group_job_rows(rows, now):
                    writer.writerow([row["installer"], row["status"], row["activity"], row["product"],
                                     row["runs"], row["count"], row["time"]])
        else:
            writer.writerow(JOB_CSV_COLUMNS)
            for owner, rows, _meta, _corp_id in all_rows:
                for row in rows:
                    writer.writerow(_job_csv_row(owner, row))
        for _owner, _rows, meta, corp_id in all_rows:
            if corp_id is not None:
                print(freshness.line("corp jobs", meta), file=sys.stderr)
        return
    for owner, rows, meta, corp_id in all_rows:
        if args.group:
            grouped = _group_job_rows(rows, now)
            table_rows = [[row["installer"], row["status"], row["activity"], row["product"],
                           row["runs"], str(row["count"]), row["time"]] for row in grouped]
            table = render.table(["installer", "status", "activity", "product", "runs", "count",
                                  "end"], table_rows) if table_rows else "(no jobs)"
        else:
            table_rows = [[row["status"], row["activity"], row["product"], row["runs"], row["time"],
                           row["installed_in"]] +
                          ([row["installer"]] if args.corp else []) for row in rows]
            headers = ["status", "activity", "product", "runs", "time", "installed in"]
            if args.corp:
                headers.append("installer")
            table = render.table(headers, table_rows) if table_rows else "(no jobs)"
        cache = f"\n{freshness.line('corp jobs', meta)}" if corp_id is not None else ""
        blocks.append(f"{owner}{cache}\n{table}")
    print("\n\n".join(blocks))


