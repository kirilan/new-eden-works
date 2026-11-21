"""The characters plugin's watch source: training transitions and the compact status table.

`skills --watch` and `neworks watch` poll it every cycle (`newedenworks.cmd_watch` runs the loop)."""

from __future__ import annotations

import argparse
import time

from .. import esi as esi_mod, render
from ..cmd_watch import WatchCycle, WatchSession
from . import skills as cmd_skills


def render_watch_status(ctxs, failures, last_good: dict[int, dict]) -> str:
    """Compact multi-character status table. A character whose fetch failed keeps
    its last-known row, marked stale, with the error under the table - healthy
    data is never dropped because a sibling went quiet."""
    rows = []
    for ctx in ctxs:
        now = ctx["now"]
        queue = ctx["queue"]
        statuses = [cmd_skills.queue_status(q, now) for q in queue]
        training = next((q for q in queue if cmd_skills.queue_status(q, now) == "training"), None)
        done_n = statuses.count("done")
        if not queue:
            queue_cell = "empty"
        else:
            queue_cell = f"{len(queue)} item" + ("" if len(queue) == 1 else "s")
            if done_n:
                queue_cell += f" ({done_n} done)"
        if training is not None:
            sid = int(training["skill_id"])
            left = render.format_duration((render.parse_ts(training["finish_date"]) - now).total_seconds())
            training_cell = f"{ctx['names'].get(sid, f'skill {sid}')} to L{training.get('finished_level', '?')}, {left} left"
        elif "blocked" in statuses:
            training_cell = "blocked (no schedule)"
        else:
            training_cell = "-"
        row = [ctx["public"].get("name") or "?", str(ctx["token"]["character_id"]), ctx["state"].state,
               queue_cell, training_cell, render.format_sp(int(ctx["skills_doc"].get("total_sp") or 0))]
        last_good[ctx["token"]["character_id"]] = {"row": row, "ts": time.time()}
        rows.append(row + ["ok"])
    now_ts = time.time()
    for f in failures:
        prev = last_good.get(f.character_id)
        if prev is not None:
            age = render.format_duration(now_ts - prev["ts"])
            rows.append(prev["row"] + [f"{age} stale"])
        else:
            rows.append([f.name, str(f.character_id), "?", "-", "-", "-", "no data yet"])
    out = [render.table(["character", "id", "clone", "queue", "training now", "sp", "fetched"], rows)]
    for f in failures:
        out.append(f"! {f.name}: {f.error}")
    return "\n".join(out)



def _with_skill_defaults(args):
    """`args` with the skills command's own defaults for anything it lacks (`neworks watch` has no
    --filter or --sort, but `--full` renders the skills view)."""
    from . import _skills
    parser = argparse.ArgumentParser(add_help=False)
    _skills(parser)
    defaults = vars(parser.parse_args([]))
    return argparse.Namespace(**{**defaults, **vars(args)})


def skills_poll(args, client: esi_mod.Esi, session: WatchSession) -> WatchCycle:
    """One turn of the training book: every stored character's skills, queue and clone state."""
    args = _with_skill_defaults(args)
    ctxs, failures = cmd_skills.gather_all(args, client=client)
    warnings = [f"skipped {f}" for f in failures]
    if args.full:
        body = ("\n" + "=" * 72 + "\n").join(cmd_skills.render_text(ctx, args) for ctx in ctxs)
    else:
        body = render_watch_status(ctxs, failures, session.last_good)
    return WatchCycle(warnings=tuple(warnings),
                      observations=tuple(cmd_skills.observation_from_ctx(c) for c in ctxs),
                      body=body,
                      # a transient ESI/network outage must not kill an overnight session
                      idle=None if ctxs else "no character data this cycle - retrying next poll")
