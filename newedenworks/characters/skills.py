"""skills, summary, plan and extract: the training book and what reaching a level costs.

Each command is a data function (`*_data`, what `--json` prints and MCP will return) and a text
function (`*_text`) that prints exactly what the command always printed."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import timedelta
from dataclasses import dataclass


from .. import alphadata, esi as esi_mod, render, sso, watchstate
from ..plugin import Result
from . import classify, planner, snapshots


def gather(args, client: esi_mod.Esi | None = None) -> dict:
    char_id = sso.resolve_character(args.char) if getattr(args, "char", None) else None
    token = sso.get_access_token(char_id)
    cfg = sso.load_config()
    # One client per command/watch session: the response cache and the ESI error-limit
    # backoff only work when state survives across characters and polls.
    client = client or esi_mod.Esi(esi_mod.default_user_agent(cfg))
    char_id = token["character_id"]

    public = client.get(f"/characters/{char_id}")
    skills_doc = client.get(f"/characters/{char_id}/skills", token=token["access_token"])
    queue = client.get(f"/characters/{char_id}/skillqueue", token=token["access_token"])
    queue.sort(key=lambda q: (q.get("queue_position", 0), q.get("start_date") or "9999"))

    now = client.now()  # ESI server time; immune to a skewed system clock
    # The skills endpoint can lag behind finished training until the character logs in;
    # apply completed queue entries on top (per ESI docs).
    trained_by_id = {int(s["skill_id"]): int(s["trained_skill_level"]) for s in skills_doc.get("skills", [])}
    completed_levels: dict[int, int] = {}
    for item in queue:
        if queue_status(item, now) == "done":
            sid = int(item["skill_id"])
            level = int(item.get("finished_level") or 0)
            if level > trained_by_id.get(sid, 0):
                completed_levels[sid] = max(completed_levels.get(sid, 0), level)

    data = alphadata.load()
    caps, grade_name = classify.alpha_caps(data, public.get("bloodline_id"))
    names = esi_mod.resolve_names(client, {int(s["skill_id"]) for s in skills_doc.get("skills", [])} | {int(q["skill_id"]) for q in queue})
    known_ids = set(names)

    extra_warnings = []
    age = alphadata.data_age_days(data)
    if age is not None and age > alphadata.STALE_DAYS:
        extra_warnings.append(
            f"local alpha caps data is {age:.0f} days old (SDE build {data['grades']['build']}) - run: neworks update-data"
        )

    rows = classify.classify_skills(skills_doc.get("skills", []), caps, names, completed_levels, known_ids=known_ids)
    state = classify.clone_state(
        rows, [q for q in queue if queue_status(q, now) != "done"], caps, names,
        now=now, known_ids=known_ids, extra_warnings=extra_warnings,
    )

    try:
        # ESI's clock, not the machine's: `week_line` and the rate calibration measure these rows
        # against `now`, and a stamp from a different clock makes that span wrong by the skew.
        snapshots.record(char_id, int(skills_doc.get("total_sp") or 0), now=now.timestamp())
    except OSError:
        pass  # history is diagnostics; never fail the view over it

    return {
        "now": now,
        "token": token,
        # The session's client travels with the context so rate sourcing can read the
        # attributes endpoint without opening a second cache/backoff state (render_json
        # picks keys explicitly, so an object in here never reaches json.dumps).
        "client": client,
        "public": public,
        "skills_doc": skills_doc,
        "queue": queue,
        "rows": rows,
        "state": state,
        "caps": caps,
        "grade_name": grade_name,
        "data_build": data["grades"]["build"],
        "names": names,
    }


FILTERS = {
    # Bucket by what alpha can actually show, independent of the current clone state:
    # a downgraded omega keeps trained levels above the cap.
    "alpha": lambda r: not r.beyond_alpha,
    "omega": lambda r: r.beyond_alpha,
}


def queue_status(item: dict, now) -> str:
    """done | training | queued | blocked (CCP issued no schedule dates - item cannot train)."""
    if not item.get("start_date") or not item.get("finish_date"):
        return "blocked"
    if render.parse_ts(item["finish_date"]) <= now:
        return "done"
    if render.parse_ts(item["start_date"]) <= now:
        return "training"
    return "queued"


def level_sp_cell(item: dict, now, status: str) -> str:
    """`204.3K/210.7K 97%` - SP into the level being trained, out of what the level costs.

    ESI publishes three SP figures per queue item and they answer three different questions:
    `level_start_sp` is where the level began, `level_end_sp` is where it ends, and
    `training_start_sp` is what the character held when `start_date` was stamped. Progress through
    a level is an SP question, so it is answered from those three - never from the fraction of
    `start_date`..`finish_date` that has elapsed. EVE restamps `start_date` on the active item
    every time the queue is rearranged, so that fraction describes the current sitting at the
    keyboard rather than the level: an hour after a reorder a level 95% trained reads as 11%, under
    a column headed `level sp`.

    SP does accrue linearly across the span, which is what makes the interpolation from
    `training_start_sp` to `level_end_sp` exact and makes the restamped stamps harmless here - the
    span and `training_start_sp` are the pair that describe each other. A queued item has trained
    nothing yet and a blocked one never will, so both keep the dash the timing column explains.
    """
    level_start, level_end = item.get("level_start_sp"), item.get("level_end_sp")
    if level_start is None or level_end is None or int(level_end) <= int(level_start):
        # Nothing measurable was published. "done" is still known to be done - it finished - but a
        # figure this column cannot source is a dash, not an invented percentage.
        return "100%" if status == "done" else "-"
    level_start, level_end = int(level_start), int(level_end)
    if status == "done":
        held: float = level_end
    elif status == "training" and item.get("training_start_sp") is not None:
        start = render.parse_opt(item.get("start_date"))
        finish = render.parse_opt(item.get("finish_date"))
        if start is None or finish is None or finish <= start:
            return "-"
        at_start = int(item["training_start_sp"])
        span = (finish - start).total_seconds()
        # Clamped because ESI's stamps and its clock can disagree by a poll: a hair past the finish
        # is 100% of the level, never 101%.
        elapsed = min(max((now - start).total_seconds(), 0.0), span)
        held = at_start + (level_end - at_start) * elapsed / span
    else:
        return "-"
    # Both figures are level-relative, like the percentage beside them. The cumulative pair ESI
    # publishes (SP held in the *skill* over SP the skill ends the level with) would put a ratio
    # next to a percentage it contradicts: a rank-1 skill halfway through L1->L2 holds 832 of 1,414
    # cumulative SP, which reads as 59% of a level that is 50% trained.
    into_level, level_cost = held - level_start, level_end - level_start
    return (f"{render.format_sp(int(round(into_level)))}/{render.format_sp(level_cost)} "
            f"{into_level / level_cost:.0%}")


def render_text(ctx: dict, args) -> str:
    now = ctx["now"]
    public = ctx["public"]
    out: list[str] = []

    bloodline_race = classify.race_name(alphadata.load()["races"]["races"].get(str(public.get("bloodline_id"))))
    state = ctx["state"]
    out.append(f"{public.get('name')} (id {ctx['token']['character_id']})  {bloodline_race}  clone grade: {ctx['grade_name']}")
    out.append(f"Clone state: {state.state}  (confidence: {state.confidence})")
    for line in state.evidence[:3]:
        out.append(f"  evidence: {line}")
    for line in state.warnings:
        out.append(f"  warning: {line}")

    total = ctx["skills_doc"].get("total_sp", 0)
    unalloc = ctx["skills_doc"].get("unallocated_sp") or 0
    out.append(f"Total SP: {render.format_sp(total)}   unallocated: {render.format_sp(unalloc)}")

    queue = ctx["queue"]
    if queue and not args.trained_only:
        out.append("")
        out.append(f"TRAINING QUEUE ({len(queue)})")
        rows = []
        for item in queue:
            sid = int(item["skill_id"])
            status = queue_status(item, now)
            start = render.parse_opt(item.get("start_date"))
            finish = render.parse_opt(item.get("finish_date"))
            progress = level_sp_cell(item, now, status)
            if status == "done":
                label, when = "done", "finished (pending login)"
            elif status == "training":
                label = "TRAINING"
                when = f"{render.format_duration((finish - now).total_seconds())} left"
            elif status == "blocked":
                label, when = "BLOCKED", "no schedule - cannot train"
            else:
                label = "queued"
                when = f"starts in {render.format_duration((start - now).total_seconds())}, {render.format_duration((finish - start).total_seconds())} long"
            cap = ctx["caps"].get(sid)
            access = "alpha" if cap is not None and item.get("finished_level", 0) <= cap else "OMEGA"
            rows.append([label, ctx["names"].get(sid, f"skill {sid}"), f"L{item.get('finished_level', '?')}", progress, when, access])
        out.append(render.table(["status", "skill", "to", "level sp", "timing", "access"], rows))

    # ESI also lists never-trained prerequisite skills; the table is about what you have.
    rows_all = [r for r in ctx["rows"] if r.trained > 0 or r.pending_completion]
    alpha_n = sum(1 for r in rows_all if not r.beyond_alpha)
    # A row the local data could not classify has no cap, so `beyond_alpha` is true for it - but the
    # warning three lines up has already said no omega claim is being made about it. Counting it as
    # omega-restricted would make one screen assert and disclaim the same thing, so it gets its own
    # bucket. The row's own cell already reads "unknown data"; the header now agrees with it.
    unknown_n = sum(1 for r in rows_all if r.unknown_data)
    omega_n = len(rows_all) - alpha_n - unknown_n
    filtered = [r for r in rows_all if FILTERS[args.filter](r)] if args.filter != "all" else rows_all

    if args.sort == "name":
        filtered.sort(key=lambda r: r.name.lower())
    elif args.sort == "level":
        filtered.sort(key=lambda r: (-r.trained, r.name.lower()))
    elif args.sort == "sp":
        filtered.sort(key=lambda r: -r.sp)

    out.append("")
    scope = "" if args.filter == "all" else f" (filter: {args.filter})"
    counts = f"alpha-trainable: {alpha_n}, omega-restricted: {omega_n}"
    if unknown_n:
        counts += f", unclassified: {unknown_n}"
    out.append(f"TRAINED SKILLS ({len(filtered)} of {len(rows_all)}; {counts}){scope}")
    table_rows = []
    for r in filtered:
        level = f"{r.trained} (active {r.active})" if r.restricted else str(r.trained)
        if r.pending_completion:
            level += " *"
        if r.cap is None:
            access = "unknown data" if r.unknown_data else "OMEGA-only"
        else:
            access = f"alpha <= {r.cap}" + (" (exceeded)" if r.trained > r.cap else "")
        table_rows.append([r.name, level, render.format_sp(r.sp), access])
    out.append(render.table(["skill", "level", "sp", "clone access"], table_rows))
    if any(r.pending_completion for r in filtered):
        out.append("* completed training, not yet reflected by ESI (applies on next login)")

    out.append("")
    out.append(f"alpha caps source: SDE build {ctx['data_build']} (neworks update-data to refresh)")
    return "\n".join(out)


def render_json(ctx: dict, args) -> str:
    return json.dumps(skills_doc(ctx), indent=2)


def skills_doc(ctx: dict) -> dict:
    now = ctx["now"]
    public = ctx["public"]
    race_id = alphadata.load()["races"]["races"].get(str(public.get("bloodline_id")))
    doc = {
        "character": {
            "id": ctx["token"]["character_id"],
            "name": public.get("name"),
            "bloodline_id": public.get("bloodline_id"),
            "race_id": race_id,
            "race": classify.race_name(race_id),
            "alpha_grade": ctx["grade_name"],
        },
        "clone_state": {
            "state": ctx["state"].state,
            "confidence": ctx["state"].confidence,
            "evidence": ctx["state"].evidence,
            "warnings": ctx["state"].warnings,
        },
        "totals": {
            "total_sp": ctx["skills_doc"].get("total_sp"),
            "unallocated_sp": ctx["skills_doc"].get("unallocated_sp"),
        },
        "queue": [
            {
                "skill_id": int(q["skill_id"]),
                "name": ctx["names"].get(int(q["skill_id"])),
                "finished_level": q.get("finished_level"),
                "start_date": q.get("start_date"),
                "finish_date": q.get("finish_date"),
                "status": queue_status(q, now),
            }
            for q in ctx["queue"]
        ],
        "skills": [
            {
                "skill_id": r.skill_id,
                "name": r.name,
                "trained_level": r.trained,
                "active_level": r.active,
                "sp": r.sp,
                "alpha_cap": r.cap,
                "access": "omega" if r.beyond_alpha else "alpha",
                "restricted": r.restricted,
                "pending_completion": r.pending_completion,
                "unknown_data": r.unknown_data,
            }
            for r in ctx["rows"]
        ],
        "data_build": ctx["data_build"],
    }
    return doc


@dataclass(frozen=True)
class FetchFailure:
    """One character whose fetch failed this cycle. str() keeps the legacy warning
    wording, so one-shot commands print exactly what they always printed."""
    character_id: int
    name: str
    error: str

    def __str__(self) -> str:
        return f"{self.name}: {self.error}"


def gather_all(args, client: esi_mod.Esi | None = None):
    """(contexts, FetchFailure list) for the selected character, or every stored one."""
    records = sso.list_characters()
    if not records:
        raise RuntimeError("not logged in — run: neworks login")
    wanted = sso.resolve_character(args.char) if getattr(args, "char", None) else None
    ctxs, failures = [], []
    for rec in records:
        if wanted is not None and int(rec["character_id"]) != wanted:
            continue
        sub_args = argparse.Namespace(**vars(args))
        sub_args.char = str(wanted if wanted is not None else rec["character_id"])
        try:
            ctxs.append(gather(sub_args, client=client))
        except (RuntimeError, esi_mod.EsiError) as err:
            failures.append(FetchFailure(int(rec["character_id"]),
                                         rec.get("character_name") or str(rec["character_id"]), str(err)))
    return ctxs, failures


def week_line(ctx) -> str:
    """SP gained since the newest snapshot at least 7 days old; honest when history is short."""
    char_id = ctx["token"]["character_id"]
    now_ts = ctx["now"].timestamp()
    cur = int(ctx["skills_doc"].get("total_sp") or 0)
    base = snapshots.latest_before(char_id, now_ts - 7 * 86400)
    if base is None:
        first = min((r["ts"] for r in snapshots.load() if r["char_id"] == char_id), default=now_ts)
        return f"SP last 7d: no baseline yet (local history starts {(now_ts - first) / 86400:.1f} days ago)"
    days = max((now_ts - base["ts"]) / 86400, 1e-9)
    delta = cur - base["total_sp"]
    return f"SP last 7d: {delta:+,} over {days:.1f} days ({render.format_sp(int(delta / days))}/day)"


def render_csv(ctxs) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["character_id", "character_name", "skill_id", "name", "trained_level", "active_level",
                     "sp", "alpha_cap", "access", "restricted", "pending_completion", "unknown_data"])
    for ctx in ctxs:
        cid = ctx["token"]["character_id"]
        cname = ctx["public"].get("name") or ""
        for r in ctx["rows"]:
            if r.trained == 0 and not r.pending_completion:
                continue  # ESI lists never-trained prerequisites; the table view filters them - CSV must agree
            writer.writerow([cid, cname, r.skill_id, r.name, r.trained, r.active, r.sp,
                             "" if r.cap is None else r.cap, "omega" if r.beyond_alpha else "alpha",
                             int(r.restricted), int(r.pending_completion), int(r.unknown_data)])
    return buf.getvalue()


def observation_from_ctx(ctx: dict) -> watchstate.CharacterObservation:
    """The persisted slice of one fetch: queue entries and settled levels only.
    Tokens stay in the ctx and never reach watchstate."""
    cid = ctx["token"]["character_id"]
    items = tuple(
        watchstate.QueueItem(
            skill_id=int(q["skill_id"]), finished_level=int(q.get("finished_level") or 0),
            status=queue_status(q, ctx["now"]), name=ctx["names"].get(int(q["skill_id"]), f"skill {int(q['skill_id'])}"),
            finish_date=q.get("finish_date"),
        )
        for q in ctx["queue"]
    )
    return watchstate.CharacterObservation(
        character_id=cid, character_name=ctx["public"].get("name") or str(cid),
        items=items, trained_levels={r.skill_id: r.trained for r in ctx["rows"]},
    )


def summary_data(args, ctx=None) -> dict:
    """One row per character: clone state, SP, queue length and what is left on the current item."""
    ctxs, failures = gather_all(args)
    for line in failures:
        print(f"warning: skipped {line}", file=sys.stderr)
    if not ctxs:
        raise RuntimeError("no character data could be fetched")
    characters = []
    for c in ctxs:
        active = next((i for i in c["queue"] if queue_status(i, c["now"]) == "training"), None)
        characters.append({
            "name": c["public"].get("name"),
            "id": c["token"]["character_id"],
            "clone_state": c["state"].state,
            "confidence": c["state"].confidence,
            "total_sp": int(c["skills_doc"].get("total_sp") or 0),
            "queue_items": len(c["queue"]),
            "current_item_seconds_left": ((render.parse_ts(active["finish_date"]) - c["now"]).total_seconds()
                                          if active else None),
        })
    return {"characters": characters, "total_sp": sum(c["total_sp"] for c in characters)}


def summary_text(data: dict, args) -> str:
    rows = []
    for c in data["characters"]:
        left = c["current_item_seconds_left"]
        rows.append([c["name"] or "?", str(c["id"]), f"{c['clone_state']} ({c['confidence']})",
                     render.format_sp(c["total_sp"]), str(c["queue_items"]),
                     render.format_duration(left) if left is not None else "-"])
    rows.append(["TOTAL", "", f"{len(data['characters'])} characters", render.format_sp(data["total_sp"]), "", ""])
    return render.table(["character", "id", "clone state", "total SP", "queue items", "current item left"], rows)


def parse_target(spec: str) -> tuple[str, int]:
    name, sep, lvl = spec.rpartition(":")
    if sep and lvl.isdigit() and 1 <= int(lvl) <= 5 and name:
        return name.strip(), int(lvl)
    return spec.strip(), 5


def load_skill_catalog() -> dict[int, alphadata.SkillInfo]:
    """The SDE skill catalog, with a message saying what to do when it is not installed."""
    try:
        return alphadata.skill_catalog()
    except FileNotFoundError:
        raise RuntimeError("no local skill catalog - run: neworks update-data") from None


def resolve_skill_id(name: str, catalog: dict[int, alphadata.SkillInfo]) -> int:
    """Resolve a target against the whole catalog, so never-trained skills work too."""
    needle = name.strip().lower()
    exact = [s for s, info in catalog.items() if info.name.lower() == needle]
    matches = exact or [s for s, info in catalog.items() if needle in info.name.lower()]
    if not matches:
        raise RuntimeError(f"no skill named '{name}' in the local skill catalog "
                           f"({len(catalog)} skills) - refresh it with: neworks update-data")
    if len(matches) > 1 and exact:
        matches = [s for s in exact if catalog[s].published] or exact
    if len(matches) > 1:
        shown = ", ".join(sorted(catalog[s].name for s in matches)[:6])
        raise RuntimeError(f"'{name}' is ambiguous: {shown}")
    return matches[0]


def character_attributes(ctx) -> dict | None:
    """The ESI attributes document, or None when this command cannot have it.

    Consent is checked with `has_feature`, not by catching a 403: a refresh token minted
    before esi-skills.read_skills.v1 joined the base scopes is refused on this route, and
    asking the stored consent what it covers beats guessing from an HTTP status. A refused
    or unreachable lookup degrades the plan to calibration instead of failing it - optional
    data must never be an error, the rule `cmd_attributes` follows.
    """
    if not sso.has_feature(ctx["token"], "attributes"):
        return None
    try:
        return ctx["client"].get(f"/characters/{ctx['token']['character_id']}/attributes",
                                 token=ctx["token"]["access_token"])
    except (esi_mod.EsiError, OSError) as err:
        print(f"warning: attributes lookup failed ({err}); calibrating the rate instead",
              file=sys.stderr)
        return None


def attribute_rates(ctx, catalog, items) -> dict[tuple[str, str], float] | None:
    """{(primary, secondary): SP/hour} for every pair `items` trains on, or None.

    One rate per attribute pair is exactly EVE's own granularity - skills sharing a pair
    share a speed - so this replaces the single calibrated figure that used to smear one
    number across pairs with different attributes (a plan holding both Command Center
    Upgrades and Planetology needed two runs with two `--rate` values). If any pair cannot
    be priced, no rates come back at all: rows silently mixing attribute-derived and
    calibrated numbers are worse than one estimate that says so.
    """
    attrs = character_attributes(ctx)
    if attrs is None:
        return None
    alpha = ctx["state"].state == "ALPHA"   # CCP halves alpha training; other states train full
    rates = {}
    for primary, secondary in sorted({(catalog[i.skill_id].primary, catalog[i.skill_id].secondary)
                                      for i in items}):
        try:
            rates[(primary, secondary)] = planner.attribute_rate(primary, secondary, attrs, alpha=alpha)
        except KeyError as exc:
            print(f"warning: the skill catalog wants attribute {exc.args[0]!r}, which the attributes "
                  "document does not carry; calibrating the rate instead", file=sys.stderr)
            return None
    return rates


def _iso(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def plan_data(args, ctx=None) -> dict:
    """What reaching the targets costs: items in training order, rates and the notes the plan needs."""
    if not args.char and len(sso.list_characters()) > 1:
        raise RuntimeError("plan needs --char (one character at a time)")
    c = gather(args)
    catalog = load_skill_catalog()
    targets: dict[int, int] = {}
    for spec in args.target:
        name, level = parse_target(spec)
        sid = resolve_skill_id(name, catalog)
        targets[sid] = max(targets.get(sid, 0), level)  # a repeated target means its deepest level
    plan = planner.build_plan(targets, catalog, {r.skill_id: r.trained for r in c["rows"]},
                              planner.scheduled_levels(c["queue"]))
    if args.rate is not None and args.rate <= 0:
        raise RuntimeError(f"--rate must be a positive SP/hour value, not {args.rate:g}")
    # A plan with no items needs no rate, so it must not be refused for want of one: "every target
    # is already covered" is the answer, and demanding --rate to print it would be an error message
    # standing in for good news. Every consumer of `rate` below is inside the items loop or guarded.
    pairs = sorted({(catalog[i.skill_id].primary, catalog[i.skill_id].secondary) for i in plan.items})
    rates: dict[tuple[str, str], float] | None = None
    rate: float | None = None
    rate_src = None
    if args.rate is not None:
        rate, rate_src = float(args.rate), "--rate override"
    elif plan.items:
        # The character's own attributes come before calibration: they price every attribute
        # pair in the plan with CCP's formula, where a calibrated figure measures one skill
        # once and then gets applied to rows driven by other attributes.
        rates = attribute_rates(c, catalog, plan.items)
        if rates is None:
            try:
                rate, rate_src = planner.calibrated_rate(c)
            except RuntimeError as err:
                if not sso.has_feature(c["token"], "attributes"):
                    who = c["public"].get("name") or c["token"]["character_id"]
                    raise RuntimeError(
                        f"{err} The stored login for this character carries no attributes consent,"
                        " so its rate cannot be computed from its attributes either - run:"
                        f" neworks login --scopes attributes  (pick '{who}' in the browser)"
                    ) from None
                raise

    # New items can only start once everything already queued has finished.
    eta = c["now"]
    pending = [render.parse_ts(q["finish_date"]) for q in c["queue"]
               if q.get("finish_date") and queue_status(q, eta) != "done"]
    backlog_end = max(pending, default=None)
    if backlog_end and backlog_end > eta:
        eta = backlog_end

    items, notes = [], []
    for item in plan.items:
        info = catalog[item.skill_id]
        hours = item.sp / (rates[(info.primary, info.secondary)] if rates is not None else rate)
        eta += timedelta(hours=hours)
        items.append({"skill_id": item.skill_id, "skill": item.name, "requested": item.requested,
                      "required_by": list(item.required_by), "trained_level": item.trained_level,
                      "from_level": item.from_level, "to_level": item.to_level, "sp": item.sp,
                      "hours": hours, "ready": _iso(eta)})
        cap = c["caps"].get(item.skill_id)
        if cap is None:
            notes.append(f"{item.name} is omega-only (absent from the alpha list)")
        elif item.to_level > cap:
            notes.append(f"{item.name} target L{item.to_level} exceeds its alpha cap {cap} - needs an omega clone to train")
        if not catalog[item.skill_id].published:
            notes.append(f"{item.name} is no longer published by CCP - its cost may be historical")
    return {
        "character": c["public"].get("name") or c["token"]["character_id"],
        "items": items,
        "covered": [{"skill": cov.name, "level": cov.level, "target": cov.target, "via_queue": cov.via_queue}
                    for cov in plan.covered],
        "total_sp": plan.total_sp,
        "queue_end": _iso(eta),
        "backlog_end": _iso(backlog_end) if backlog_end and backlog_end > c["now"] else None,
        "notes": notes,
        # Only attribute-derived rates are halved for alpha; read the clone state only then.
        "alpha": c["state"].state == "ALPHA" if rates is not None else None,
        "attribute_pairs": [list(pair) for pair in pairs],
        "rates": ([{"primary": a, "secondary": b, "sp_per_hour": rates[(a, b)]} for a, b in pairs]
                  if rates is not None else None),
        "rate": {"sp_per_hour": rate, "source": rate_src} if rates is None and rate is not None else None,
    }


def plan_text(data: dict, args) -> str:
    out = [f"{data['character']}: {len(data['items'])} item(s) to train"
           + (f", {len(data['covered'])} already covered" if data["covered"] else "")]
    rows = []
    for item in data["items"]:
        required_by = item["required_by"]
        why = "requested" if item["requested"] else "for " + ", ".join(required_by[:2]) + (
            f" +{len(required_by) - 2}" if len(required_by) > 2 else "")
        rows.append([item["skill"], why, f"L{item['from_level']}" + ("*" if item["from_level"] > item["trained_level"] else ""),
                     f"L{item['to_level']}", render.format_sp(item["sp"]), render.format_duration(item["hours"] * 3600),
                     render.parse_ts(item["ready"]).strftime("%b %d %H:%M")])
    if data["items"]:
        out.append(render.table(["skill", "why", "now", "target", "SP cost", "time", "ready"], rows))
        out.append(f"\ntotal: {render.format_sp(data['total_sp'])} SP; queue would end "
                   f"~{render.parse_ts(data['queue_end']).strftime('%Y-%m-%d %H:%M')} UTC")
    if data["backlog_end"]:
        out.append(f"note: the existing queue drains first (finishes ~{render.parse_ts(data['backlog_end']).strftime('%Y-%m-%d %H:%M')} UTC);"
                   " new items start after that")
    out.extend(f"note: {note}" for note in data["notes"])
    for covered in data["covered"]:
        how = (f"reaches L{covered['level']} in the existing queue" if covered["via_queue"]
               else f"is already trained to L{covered['level']}")
        out.append(f"note: {covered['skill']} {how} - its L{covered['target']} requirement costs nothing")
    if any(item["from_level"] > item["trained_level"] for item in data["items"]):
        out.append("note: * = an existing queue entry raises this skill to that level before the item starts")
    if data["rates"] is not None:
        alpha_tag = ", alpha half rate" if data["alpha"] else ""
        for entry in data["rates"]:
            out.append(f"rate: {entry['sp_per_hour']:,.0f} SP/hour"
                       f" (character attributes{alpha_tag}: {entry['primary']}/{entry['secondary']})")
        out.append("note: attribute rates include fitted implant bonuses - ESI reports the effective"
                   " attributes the trainer uses (verified against live queue timings 2026-09-13)")
    elif data["rate"] is not None:
        out.append(f"rate: {data['rate']['sp_per_hour']:,.0f} SP/hour ({data['rate']['source']})")
    if data["rates"] is None and len(data["attribute_pairs"]) > 1:
        listed = ", ".join(f"{primary}/{secondary}" for primary, secondary in data["attribute_pairs"])
        out.append(f"note: one rate is applied to every row, but these skills are driven by {listed};"
                   " rows whose attributes differ from the calibrated skill are estimates")
    out.append("note: costs are rank-based and exact; remaps or implant changes while training shift real time")
    return "\n".join(out)


def extract_data(args, ctx=None) -> dict:
    """How many Skill Extractors a character can run, what each costs in re-training, and the rules."""
    if not args.char and len(sso.list_characters()) > 1:
        raise RuntimeError("extract needs --char (one character at a time)")
    c = gather(args)
    total = int(c["skills_doc"].get("total_sp") or 0)
    unalloc = int(c["skills_doc"].get("unallocated_sp") or 0)
    allocated = total - unalloc
    try:
        rate, rate_src = planner.calibrated_rate(c)
    except RuntimeError:
        rate, rate_src = None, "unknown"
    p = planner.extraction_plan(allocated, total, rate)
    return {
        "character": c["public"].get("name") or c["token"]["character_id"],
        "allocated_sp": allocated, "unallocated_sp": unalloc, "total_sp": total,
        "extractors": int(p["count"]), "reason": p.get("reason"),
        "retrain_days_each": p.get("retrain_days_each"), "rate": rate, "rate_source": rate_src,
        "injector_value": p.get("injector_value"),
        "extractor_sp": planner.EXTRACTION["extractor_sp"], "floor_sp": planner.EXTRACTION["floor_sp"],
        "rules_warning": planner.extraction_rules_warning(c["now"]),
        "rules_source": planner.EXTRACTION["source"], "rules_verified": planner.EXTRACTION["verified"],
    }


def extract_text(data: dict, args) -> str:
    out = [f"{data['character']}: {render.format_sp(data['allocated_sp'])} trained (allocated) SP, "
           f"{render.format_sp(data['unallocated_sp'])} unallocated"]
    if data["extractors"] == 0:
        out.append(f"cannot extract: {data['reason']}")
    else:
        out.append(f"can run {data['extractors']} Skill Extractor(s) of {data['extractor_sp']:,} SP each "
                   f"(must keep {data['floor_sp']:,} trained; queued/training SP is not extractable)")
        if data["retrain_days_each"]:
            out.append(f"each extractor costs about {data['retrain_days_each']:.1f} days of re-training at "
                       f"{data['rate']:,.0f} SP/hour ({data['rate_source']})")
        out.append(f"re-injecting here yields only {data['injector_value']:,} SP per injector at "
                   f"{render.format_sp(data['total_sp'])} total SP "
                   f"(tiers: 500k below 5M, 400k to 50M, 300k to 80M, else 150k - lower-SP alts gain more)")
    if data["rules_warning"]:
        out.append("warning: " + data["rules_warning"])
    else:
        out.append(f"rules per CCP ({data['rules_source']}), verified {data['rules_verified']}")
    return "\n".join(out)


class SkillsResult(Result):
    """The gathered characters: `--json` is today's document, text and CSV render the same contexts."""

    def __init__(self, ctxs: list[dict], args) -> None:
        self.ctxs, self.args = ctxs, args

    def to_json(self):
        docs = [skills_doc(c) for c in self.ctxs]
        return docs[0] if len(docs) == 1 else docs


def skills_data(args, ctx=None) -> SkillsResult:
    ctxs, failures = gather_all(args)
    for line in failures:
        print(f"warning: skipped {line}", file=sys.stderr)
    if not ctxs:
        raise RuntimeError("no character data could be fetched")
    return SkillsResult(ctxs, args)


def skills_text(result: SkillsResult, args) -> str:
    blocks = [render_text(c, args) + (("\n" + week_line(c)) if args.week else "") for c in result.ctxs]
    return ("\n" + "=" * 72 + "\n").join(blocks)


def skills_csv(result: SkillsResult, args) -> str:
    return render_csv(result.ctxs)


def skills_watch(args):
    """`skills --watch`: the watch loop over every watch source the arguments leave on."""
    from .. import cmd_watch   # local: the characters' watch source imports this module
    return cmd_watch.watch(args)
