"""Industry's alert source: jobs about to finish, a summary when a finish window opens, and batching.

The per-job alerts (`evaluate_announced`, settings `job_alerts` / `job_notice_minutes`) announce each
job once when it has at most the notice left, then keep it standing for the core's reminder
(`newedenworks.notify`, the same clock as the orders still behind) until it is delivered: the job
leaving its installer's list. A paused job is not news. Jobs finish one by one
over an hour or two, so on their own they make one message per run while a round lands. This module
decides, per run, two things on top (`evaluate` is pure; `cmd_alerts` reads and sends):

- *window summary*: on the first run inside each finish window (settings `jobs.windows`, else the
  operation file's `planning.finish_windows`; none: no summaries), if any job is ready or any slot is
  free - ready jobs per character, free slots per character, and the jobs still ending inside the
  window. It covers every job news of that run: the jobs it lists count as announced.
- *batch*: a job the per-job alerts would announce now is held back while another job of the same site
  ends within `jobs.batch_minutes` (default 45), unless the oldest held job of that site has waited
  `jobs.max_wait_minutes` (default 90). Then everything of the site goes out in one message.
- *quiet hours*: no job news at all (not even silent); the next window summary covers the night.

`exclude_characters` applies; `jobs.enabled: false` turns this module off (the per-job alerts stay as
they are). State (`job-alerts.json`, state directory): the announced jobs still undelivered, the held
job ids with the time they were first held, and the last window summarised. It is saved only after
Telegram accepted the run's message. (The announced jobs were kept in the order alerts' state before
notify became the core's; the first run reads them from there.)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import job_times
from .. import notify, paths, storage

STATE_NAME = "job-alerts.json"


def empty_state() -> dict:
    return {"held": {}, "last_window": None}


def state_path() -> str:
    return os.path.join(paths.state_dir(), STATE_NAME)


def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return empty_state()
    return doc if isinstance(doc, dict) else empty_state()


def save_state(state: dict) -> None:
    storage.atomic_write_json(state_path(), state)


@dataclass
class JobNews:
    """What the job part of one run adds: a summary to send (or None) and the job ids to hold back."""
    summary: dict | None = None
    hold: set[int] = field(default_factory=set)


def _end(job: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(job.get("end")).replace("Z", "+00:00"))
    except ValueError:
        return None


def window_spec(settings, planning_windows: str | None) -> str | None:
    """The finish windows in force: the alert settings', else the operation file's, else none."""
    if settings.jobs_windows is not None:
        return settings.jobs_windows or None
    return planning_windows or None


def current_window(now: datetime, spec: str | None, zone_name: str) -> tuple[str, datetime] | None:
    """(key, end) of the finish window `now` falls in, in the settings' zone; None outside every window.

    The key names the day and the window (`2030-01-02 08:00-10:00`), so each window is summarised once."""
    if not spec:
        return None
    tz = job_times.zone(zone_name)
    local = now.astimezone(tz)
    minute = local.hour * 60 + local.minute
    for lo, hi in job_times.parse_windows(spec):
        if lo <= minute < hi:
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            key = f"{local:%Y-%m-%d} {lo // 60:02d}:{lo % 60:02d}-{hi // 60:02d}:{hi % 60:02d}"
            return key, (midnight + timedelta(minutes=hi)).astimezone(now.tzinfo)
    return None


def summary_due(state: dict, settings, planning_windows: str | None, now: datetime) -> bool:
    """Whether this run opens a window not summarised yet (the caller then reads the slots)."""
    if not (settings.job_alerts and settings.jobs_enabled) or settings.quiet(now):
        return False
    window = current_window(now, window_spec(settings, planning_windows), settings.timezone)
    return window is not None and window[0] != state.get("last_window")


def evaluate(state: dict, jobs: list[dict], slots: list[dict] | None, settings, now: datetime, *,
             announced: set[int], planning_windows: str | None = None,
             sites: dict[str, str] | None = None) -> tuple[dict, JobNews]:
    """(new state, news) for one run.

    `jobs` are this run's undelivered jobs (`job_id`, `installer`, `activity`, `end`, `status`, ...),
    `slots` the characters' slot rows (`character_name`, `slots: {manufacturing|science: {free, ...}}`)
    or None when they were not read, `announced` the job ids the per-job alerts announced already, and
    `sites` maps a character name (case-folded) to its site; characters outside every site form one group."""
    if not (settings.job_alerts and settings.jobs_enabled):
        return state, JobNews()
    ts = now.timestamp()
    held_before = {int(k): float(v) for k, v in (state.get("held") or {}).items()}
    sites = sites or {}
    live = [j for j in jobs if j.get("status") != "paused" and not settings.excluded_character(j.get("installer"))]
    notice = settings.job_notice_minutes * 60

    def due(job: dict) -> bool:
        end = _end(job)
        return end is not None and end.timestamp() - ts <= notice

    candidates = [j for j in live if int(j["job_id"]) not in announced and due(j)]
    new_state = {"held": {}, "last_window": state.get("last_window")}

    if settings.quiet(now):
        hold = {int(j["job_id"]) for j in candidates}
        new_state["held"] = {str(i): held_before.get(i, ts) for i in hold}
        return new_state, JobNews(hold=hold)

    window = current_window(now, window_spec(settings, planning_windows), settings.timezone)
    if window is not None and window[0] != state.get("last_window"):
        new_state["last_window"] = window[0]
        key, window_end = window
        ready = [j for j in live if j.get("status") == "ready" or ((_end(j) or now) <= now)]
        ready_ids = {int(j["job_id"]) for j in ready}
        ending = sorted((j for j in live if int(j["job_id"]) not in ready_ids and (
                         (_end(j) is not None and _end(j) <= window_end) or j in candidates)),
                        key=lambda j: _end(j) or now)
        free = []
        for row in slots or []:
            name = row.get("character_name")
            if settings.excluded_character(name):
                continue
            cells = {kind: (row.get("slots") or {}).get(kind) or {} for kind in ("manufacturing", "science")}
            counts = {kind: cell.get("free") for kind, cell in cells.items() if cell.get("free")}
            if counts:
                free.append({"character": name, **counts})
        if ready or free:
            return new_state, JobNews(summary={"window": key.split(" ", 1)[1], "ready": ready, "free": free,
                                               "ending": ending, "slots_read": slots is not None})
        # nothing to say at the window's start: fall through to the batch rule

    groups: dict[str, list[dict]] = {}
    for job in candidates:
        groups.setdefault(sites.get(str(job.get("installer") or "").casefold(), ""), []).append(job)
    hold: set[int] = set()
    horizon = ts + settings.batch_minutes * 60
    for site, members in groups.items():
        ids = {int(j["job_id"]) for j in members}
        oldest = min(held_before.get(i, ts) for i in ids)
        more = any(int(j["job_id"]) not in ids and int(j["job_id"]) not in announced
                   and sites.get(str(j.get("installer") or "").casefold(), "") == site
                   and (_end(j) is not None and ts < _end(j).timestamp() <= horizon)
                   for j in live)
        if more and ts - oldest < settings.max_wait_minutes * 60:
            hold |= ids
    new_state["held"] = {str(i): held_before.get(i, ts) for i in hold}
    return new_state, JobNews(hold=hold)


def render_summary(summary: dict, settings, now: datetime) -> str:
    """The window message: ready jobs per character and facility, free slots, jobs still ending in the
    window. Job lines are `job_lines`: counts per activity, no products."""
    tz = job_times.zone(settings.timezone)
    free_total = sum(row.get("manufacturing", 0) + row.get("science", 0) for row in summary["free"])
    head = [f"{len(summary['ready'])} ready"] + ([f"{free_total} free slot{'' if free_total == 1 else 's'}"]
                                                 if free_total else [])
    parts = [f"EVE jobs: {summary['window']} window, " + ", ".join(head)]
    if summary["ready"]:
        parts.append("\n".join(["Ready to deliver:"] + job_lines(summary["ready"])))
    if summary["free"]:
        lines = ["Free slots:"]
        for row in summary["free"]:
            cells = [f"{row[kind]} {kind}" for kind in ("manufacturing", "science") if row.get(kind)]
            lines.append(f"   {row['character']}: {', '.join(cells)}")
        parts.append("\n".join(lines))
    elif not summary.get("slots_read", True):
        parts.append("Free slots: not read this run")
    if summary["ending"]:
        def clock(jobs: list[dict]) -> str:
            known = sorted(end.astimezone(tz).strftime("%H:%M") for end in map(_end, jobs) if end)
            return "?" if not known else known[0] if known[0] == known[-1] else f"{known[0]}-{known[-1]}"
        parts.append("\n".join(["Ending in the window:"] + job_lines(summary["ending"], clock)))
    return "\n\n".join(parts)


JOB_KEYS = ("owner_key", "installer", "activity", "product_id", "runs", "end")


def evaluate_announced(previous: dict[int, dict], jobs: list[dict], read: set[str], settings,
                       ts: float) -> tuple[dict[int, dict], list[dict], list[dict]]:
    """(the announced jobs still undelivered, jobs newly within the notice, announced jobs still waiting).

    Only announced jobs are remembered. One missing from its installer's list, when that list was
    read, has been delivered (or cancelled): it is dropped without a message."""
    kept: dict[int, dict] = {}
    soon: list[dict] = []
    waiting: list[dict] = []
    seen = set()
    for job in jobs:
        job_id = int(job["job_id"])
        seen.add(job_id)
        if settings.excluded_character(job.get("installer")):
            continue
        before = previous.get(job_id)
        if job.get("status") == "paused":
            if before is not None:
                kept[job_id] = before
            continue
        end = _end_ts(job)
        entry = {key: job.get(key) for key in JOB_KEYS}
        if before is not None:
            kept[job_id] = {**entry, "announced_at": before.get("announced_at")}
            waiting.append(job)
        elif end is not None and end - ts <= settings.job_notice_minutes * 60:
            kept[job_id] = {**entry, "announced_at": ts}
            soon.append(job)
    for job_id, before in previous.items():
        if job_id not in seen and before.get("owner_key") not in read:
            kept[job_id] = before           # unread this run: not evidence of a delivery
    return kept, soon, waiting


# Where industry's pieces sit among every source's (`notify.compose`): after the order news, and before
# the orders still behind.
RANK_SOON, RANK_WAITING = 20, 30


def parts(soon: list[dict], waiting: list[dict], summary: dict | None, settings, now: datetime) -> notify.Parts:
    """Industry's part of the message: the jobs ending, the jobs still to deliver (when the reminder
    is due), and the window summary as a message of its own."""
    ts = now.timestamp()
    heads, blocks = [], []
    if soon:
        heads.append((RANK_SOON, f"{notify.plural(len(soon), 'job')} ending"))
        blocks.append((RANK_SOON, "\n".join(job_lines(soon, lambda jobs: time_left(jobs, ts)))))
    if waiting:
        def remaining(jobs: list[dict]) -> str | None:
            text = time_left(jobs, ts)
            return None if text == "ready" else text
        heads.append((RANK_SOON + 1, f"{notify.plural(len(waiting), 'job')} to deliver"))
        # The label only when the message also has jobs just ending: alone, the title says it.
        label = ["Still to deliver:"] if soon else []
        blocks.append((RANK_WAITING, "\n".join(label + job_lines(waiting, remaining))))
    counts = f"{len(soon)} jobs ending, {len(waiting)} jobs to deliver"
    if summary:
        counts += f", window summary {summary['window']}"
    return notify.Parts(label="jobs", content=bool(soon or waiting), heads=heads, blocks=blocks,
                        after=[render_summary(summary, settings, now)] if summary else [], counts=counts)


# -- job lines and times --

def _end_ts(job: dict) -> float | None:
    try:
        return datetime.fromisoformat(str(job.get("end")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None



# The system part of a place name; the core's, since order alerts say where the same way.
_where = notify.where


def job_lines(jobs: list[dict], when=None) -> list[str]:
    """`⚙ Where`, then one line per installer: `   Who: 10 invention, 2 copying · when`.

    The products are left out on purpose: one delivers per character and facility, and the industry
    window lists the items. `when(jobs)` is the text after the counts of one line (None: nothing).
    Facilities and installers come in the order of their first job to finish."""
    def first_end(members: list[dict]) -> float:
        return min((end for end in (_end_ts(job) for job in members) if end is not None), default=0.0)

    places: dict[str, dict[str, list[dict]]] = {}
    for job in jobs:
        places.setdefault(_where(job), {}).setdefault(str(job.get("installer") or "?"), []).append(job)
    lines = []
    for where, people in sorted(places.items(), key=lambda kv: (first_end(sum(kv[1].values(), [])), kv[0])):
        lines.append(f"⚙ {where or 'Jobs'}")
        for who, members in sorted(people.items(), key=lambda kv: (first_end(kv[1]), kv[0])):
            counts: dict[str, int] = {}
            for job in members:
                activity = str(job.get("activity") or "industry")
                counts[activity] = counts.get(activity, 0) + 1
            what = ", ".join(f"{n} {activity}"
                             for activity, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
            tail = when(members) if when else None
            lines.append(f"   {who}: {what}" + (f" · {tail}" if tail else ""))
    return lines


def _left(job: dict, ts: float) -> float | None:
    end = _end_ts(job)
    return None if end is None else end - ts


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"


def _span(low: float, high: float) -> str:
    """`12 min`, `5–12 min` or `50 min–1 h 10 min` for the shortest and longest time left."""
    first, last = _minutes(low), _minutes(high)
    if first == last:
        return last
    return f"{first.removesuffix(' min')}–{last}" if round(high / 60) < 60 else f"{first}–{last}"


def time_left(jobs: list[dict], ts: float) -> str:
    """`ready`, `in 12 min`, `in 5–12 min` or `2 ready, rest in 12 min` for a group of jobs."""
    left = [x for x in (_left(job, ts) for job in jobs) if x is not None]
    later = [x for x in left if x > 0]
    if not later:
        return "ready"
    if len(later) < len(left):
        return f"{len(left) - len(later)} ready, rest in {_minutes(max(later))}"
    return f"in {_span(min(later), max(later))}"
