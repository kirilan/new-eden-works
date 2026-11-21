"""Measured industry job times and run counts that finish inside chosen local-time windows.

`jobs --times` groups past and running jobs by activity, product, installer and facility and reports the median
hours per run (per attempt for invention, per copy for copying) - the real figure after skills,
implants, facility and blueprint TE, which the blueprint's base time is not. With `--finish-window`
it also says how many runs, installed at `--start`, end inside a window such as 08:00-10:00.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable, Sequence

# Re-exported: industry code and its callers read these as job_times.zone / .parse_windows.
# Re-exported: industry code and its callers read these as job_times.zone / .parse_windows.
from ..localtime import parse_windows, zone, zone_database_available  # noqa: F401

# Statuses whose start/end span is a real run time. A cancelled or reverted job's end_date is the
# planned end of a job that never ran to it; a paused job's clock stopped.
TIMED_STATUSES = {"active", "ready", "delivered"}


def parse_start(spec: str | None, tz: tzinfo, now: datetime) -> datetime:
    """Install time: None = now; HH:MM = the next such local time (up to an hour back counts as today);
    anything else an ISO timestamp."""
    if not spec:
        return now
    if ":" in spec and len(spec) <= 5:
        hh, mm = spec.split(":")
        local_now = now.astimezone(tz)
        local = local_now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if local < local_now - timedelta(hours=1):
            local += timedelta(days=1)
        return local.astimezone(timezone.utc)
    moment = datetime.fromisoformat(spec.replace("Z", "+00:00"))
    return (moment if moment.tzinfo else moment.replace(tzinfo=tz)).astimezone(timezone.utc)


def parse_since(spec: str, tz: tzinfo, now: datetime) -> datetime:
    """`jobs --since`: HH:MM = the most recent such local time (today, or yesterday when it is still
    ahead of now); anything else an ISO timestamp, read in `tz` when it carries no offset."""
    text = spec.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hh, mm = (int(part) for part in text.split(":"))
        if hh > 23 or mm > 59:
            raise RuntimeError(f"--since time '{spec}' is not a time of day")
        local_now = now.astimezone(tz)
        local = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if local > local_now:
            local -= timedelta(days=1)
        return local.astimezone(timezone.utc)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError(f"--since expects HH:MM or an ISO timestamp such as 2026-01-31T08:00, got '{spec}'") from None
    return (moment if moment.tzinfo else moment.replace(tzinfo=tz)).astimezone(timezone.utc)


def in_window(moment: datetime, windows: Sequence[tuple[int, int]], tz: tzinfo) -> bool:
    local = moment.astimezone(tz)
    minute = local.hour * 60 + local.minute
    return any(lo <= minute < hi for lo, hi in windows)


def window_fits(start: datetime, hours: float, windows: Sequence[tuple[int, int]], tz: tzinfo, *,
                max_hours: float, max_runs: int | None = None) -> list[dict]:
    """Run counts ending inside a window, longest first: the largest count per window occurrence.

    A longer job that ends in a window beats a shorter one ending at night, so each window keeps the
    most runs that still land in it; callers cap by the runs left on a blueprint with `max_runs`."""
    if hours <= 0:
        return []
    fits: dict[tuple, dict] = {}
    runs = 1
    while runs * hours <= max_hours and (max_runs is None or runs <= max_runs):
        end = start + timedelta(hours=runs * hours)
        if in_window(end, windows, tz):
            local = end.astimezone(tz)
            key = (local.date(), next(i for i, (lo, hi) in enumerate(windows)
                                      if lo <= local.hour * 60 + local.minute < hi))
            fits[key] = {"runs": runs, "end": end.isoformat().replace("+00:00", "Z")}
        runs += 1
    return sorted(fits.values(), key=lambda fit: -fit["runs"])


def median_times(rows: Iterable[dict], since: datetime, facility_names: dict[int, str] | None = None,
                 facility: int | None = None) -> list[dict]:
    """Median hours per run by (activity, product, installer, facility) over timed jobs started since `since`.

    Rows are `jobs` rows (`_job_rows` output). Invention products are the invented blueprint; the
    ' Blueprint' suffix is kept so an invention row never reads as the manufactured item. The facility
    is part of the key because a structure's rigs change the time per run: an NPC station's median applied to
    a job in a rigged null-sec structure would size it for the wrong finish window. `facility_names`
    labels known facility ids (the operation file's names); `facility` keeps only that one."""
    names = facility_names or {}
    groups: dict[tuple, list[float]] = {}
    for row in rows:
        start = row.get("start")
        if (row.get("status") not in TIMED_STATUSES or not row.get("hours_per_run") or start is None
                or start < since):
            continue
        facility_id = row.get("facility_id")
        if facility is not None and facility_id != facility:
            continue
        label = names.get(facility_id) or row.get("installed_in") or (
            f"facility {facility_id}" if facility_id is not None else "-")
        key = (row["activity"], row.get("product") or f"type {row.get('product_id')}",
               row.get("installer") or f"character {row.get('installer_id')}", facility_id, label)
        groups.setdefault(key, []).append(float(row["hours_per_run"]))
    out = [{"activity": activity, "product": product, "installer": installer,
            "facility_id": facility_id, "facility": label,
            "hours_per_run": statistics.median(values), "min_hours": min(values),
            "max_hours": max(values), "jobs": len(values)}
           for (activity, product, installer, facility_id, label), values in groups.items()]
    out.sort(key=lambda r: (r["activity"], r["product"].casefold(), r["hours_per_run"]))
    return out


def site_times(times: Sequence[dict], op, site) -> tuple[list[dict], list[dict]]:
    """Hours per run where `site` does each activity: `(rows, unscaled)`.

    `times` is `median_times` output (one row per activity, product, installer and facility). Per
    (activity, product, installer) the answer is the median measured at the site's facility for that
    activity (`site.facility_for`), with `"scaled_from": None`. Without such jobs it is the median from
    another facility of the operation file that has a `location_id`, converted by the two facilities'
    time multipliers for the activity (`hours x home / source`; with several, the one with the most
    jobs) and `"scaled_from": <source key>`. A product measured only somewhere the file does not know
    gives no row: it is listed in `unscaled`, because a structure's unknown rigs would make any scaling
    a guess - the error this exists to prevent."""
    known = {f.location_id: (key, f) for key, f in op.facilities.items() if f.location_id}
    homes: dict[str, tuple[str, object]] = {}

    def home(activity: str):
        if activity not in homes:
            key = site.facility_for(activity)
            facility = op.facility(key)
            if not facility.location_id:
                raise RuntimeError(f"facility {key} (where site {site.name} does {activity}) has no "
                                   f"location_id in the operation file - add it to measure times there")
            homes[activity] = (key, facility)
        return homes[activity]

    groups: dict[tuple, list[dict]] = {}
    for row in times:
        groups.setdefault((row["activity"], row["product"], row["installer"]), []).append(row)
    out: list[dict] = []
    unscaled: list[dict] = []
    for (activity, product, installer), rows in groups.items():
        home_key, home_facility = home(activity)
        at_home = [r for r in rows if r.get("facility_id") == home_facility.location_id]
        if at_home:
            out.append({**at_home[0], "facility": home_key, "scaled_from": None})
            continue
        sources = [r for r in rows if r.get("facility_id") in known]
        if not sources:
            unscaled.extend({"activity": activity, "product": product, "installer": installer,
                             "facility_id": r.get("facility_id"), "facility": r.get("facility"),
                             "jobs": r.get("jobs")} for r in rows)
            continue
        source = max(sources, key=lambda r: (r["jobs"], -(r.get("facility_id") or 0)))
        source_key, source_facility = known[source["facility_id"]]
        factor = home_facility.time_multiplier(activity) / source_facility.time_multiplier(activity)
        out.append({**source, "facility_id": home_facility.location_id, "facility": home_key,
                    "hours_per_run": source["hours_per_run"] * factor,
                    "min_hours": source["min_hours"] * factor, "max_hours": source["max_hours"] * factor,
                    "scaled_from": source_key})
    out.sort(key=lambda r: (r["activity"], r["product"].casefold(), r["hours_per_run"]))
    unscaled.sort(key=lambda r: (r["activity"], r["product"].casefold(), r["installer"]))
    return out, unscaled
