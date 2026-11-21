"""attributes, standings, travel and implants: what a character is, where it is and what it wears.

Each command is a data function (`*_data`, what `--json` prints) and a text function (`*_text`) that
prints exactly what the command always printed; standings, travel and implants also keep their CSV.
"""

from __future__ import annotations

import csv
import io

from .. import esi as esi_mod, render, sso
from ..exports import hint, name_or_id, targets

ATTRIBUTE_NAMES = ("perception", "intelligence", "memory", "charisma", "willpower")


# -- attributes -----------------------------------------------------------------------------------

def attributes_data(args, ctx=None) -> dict:
    records = [sso.resolve_character(args.char)] if args.char else [r["character_id"] for r in sso.list_characters()]
    if not records:
        raise RuntimeError("not logged in - run: neworks login")
    client = esi_mod.Esi(esi_mod.default_user_agent(sso.load_config()))
    characters, missing = [], []
    for cid in records:
        rec = sso.get_access_token(cid)
        name = rec.get("character_name") or str(cid)
        if not sso.has_feature(rec, "attributes"):
            missing.append(name)
            continue
        a = client.get(f"/characters/{cid}/attributes", token=rec["access_token"])
        # Live ESI (checked on two stored characters, 2026-09-13) sends `bonus_remaps`. Neither
        # the older `accumulated_remaps` nor `accelerator_bonus_days` read here is in the current
        # spec and neither key arrives, so every character used to print "remaps available: ?".
        # Cerebral accelerators still exist in-game; ESI just no longer exposes the day counter.
        characters.append({"name": name, "id": cid,
                           "attributes": {key: a.get(key) for key in ATTRIBUTE_NAMES},
                           "bonus_remaps": a.get("bonus_remaps", 0),
                           "last_remap": (a.get("last_remap_date") or "")[:10] or None})
    return {"characters": characters, "without_consent": missing}


def attributes_text(data: dict, args) -> str:
    blocks = []
    for c in data["characters"]:
        attrs = "  ".join(f"{k[:3].upper()} {c['attributes'].get(k)}" for k in ATTRIBUTE_NAMES)
        blocks.append(f"{c['name']} (id {c['id']})\n  {attrs}\n"
                      f"  remaps available: {c['bonus_remaps']}   last remap: {c['last_remap'] or 'never'}")
    for name in data["without_consent"]:
        blocks.append(f"{name}: no skills consent - run: neworks login  (pick '{name}' in the browser)")
    if data["characters"]:
        # These are not base values, which matters before spending a remap on them; the proof
        # is in planner.attribute_rate, where the same document prices live training exactly.
        blocks.append("note: ESI reports effective attributes - fitted implant bonuses are included")
    return "\n\n".join(blocks)


# -- standings ------------------------------------------------------------------------------------

def _standing_entries(doc: list[dict], names: dict[int, str]) -> list[tuple[str, int, str, float]]:
    kind_labels = {"agent": "agent", "npc_corp": "npc corp", "faction": "faction"}
    entries = [
        (kind_labels.get(e.get("from_type"), e.get("from_type") or "unknown"),
         int(e["from_id"]), name_or_id(names, e["from_id"]), float(e.get("standing") or 0))
        for e in doc
    ]
    return sorted(entries, key=lambda entry: entry[2])


def standings_data(args, ctx=None) -> dict:
    client, chars, hints = targets(args, [("standings", "esi-characters.read_standings.v1")])
    characters = []
    for tok, public in chars:
        doc = client.get(f"/characters/{tok['character_id']}/standings", token=tok["access_token"])
        ids = {int(entry["from_id"]) for entry in doc}
        names = esi_mod.resolve_names(client, ids) if ids else {}
        characters.append({"name": public.get("name") or str(tok["character_id"]), "id": tok["character_id"],
                           "standings": [{"kind": kind, "from_id": fid, "name": fname, "standing": standing}
                                         for kind, fid, fname, standing in _standing_entries(doc, names)]})
    return {"hints": list(hints), "characters": characters}


def standings_text(data: dict, args) -> str:
    blocks = list(data["hints"])
    for c in data["characters"]:
        entries = c["standings"]
        table = render.table(["kind", "name", "standing"],
                             [[e["kind"], e["name"], f"{e['standing']:+.2f}"] for e in entries]) \
            if entries else "(no standings recorded)"
        blocks.append(f"{c['name']} (id {c['id']})\n{table}")
    return "\n\n".join(blocks)


def standings_csv(data: dict, args) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["character", "kind", "from_id", "name", "standing"])
    for c in data["characters"]:
        for e in c["standings"]:
            writer.writerow([c["name"], e["kind"], e["from_id"], e["name"], f"{e['standing']:+.2f}"])
    return buf.getvalue()


# -- travel ---------------------------------------------------------------------------------------

def travel_data(args, ctx=None) -> dict:
    client, chars, hints = targets(args, [("clones", "esi-clones.read_clones.v1"),
                                          ("location", "esi-location.read_location.v1")])
    characters = []
    for tok, public in chars:
        cid = tok["character_id"]
        cname = public.get("name") or str(cid)
        granted = set(tok.get("scopes") or [])
        entry = {"name": cname, "id": cid, "location_error": None, "location_hint": None, "clones_error": None,
                 "current": None, "home": None, "last_clone_jump": None, "jump_clones": []}
        loc = None
        if "esi-location.read_location.v1" in granted:
            try:
                loc = client.get(f"/characters/{cid}/location", token=tok["access_token"]) or {}
            except esi_mod.AuthError as err:
                entry["location_error"] = str(err)
        else:
            entry["location_hint"] = hint(cname, "location")
        clones_doc = {}
        try:
            clones_doc = client.get(f"/characters/{cid}/clones", token=tok["access_token"]) or {}
        except esi_mod.AuthError as err:
            entry["clones_error"] = str(err)
        ids = set()
        if loc:
            ids |= {int(v) for v in (loc.get("solar_system_id"), loc.get("station_id"), loc.get("structure_id")) if v}
        home = clones_doc.get("home_location") or {}
        if home:
            ids.add(int(home["location_id"]))
        jump_clones = clones_doc.get("jump_clones") or []
        for jc in jump_clones:
            ids.add(int(jc["location_id"]))
            ids |= {int(i) for i in (jc.get("implants") or [])}
        names = esi_mod.resolve_names(client, ids) if ids else {}
        if loc:
            entry["current"] = {"where": name_or_id(names, loc.get("station_id") or loc.get("structure_id")),
                                "system": name_or_id(names, loc.get("solar_system_id"))}
        if home:
            entry["home"] = name_or_id(names, home["location_id"])
        entry["last_clone_jump"] = clones_doc.get("last_clone_jump_date")
        # In ESI's order: the CSV lists clones as ESI sends them, the table sorts them by name.
        entry["jump_clones"] = [{"name": jc.get("name"), "location": name_or_id(names, jc["location_id"]),
                                 "implants": [name_or_id(names, i) for i in (jc.get("implants") or [])]}
                                for jc in jump_clones]
        characters.append(entry)
    return {"hints": list(hints), "characters": characters}


def travel_text(data: dict, args) -> str:
    blocks = list(data["hints"])
    for c in data["characters"]:
        lines = [f"{c['name']} (id {c['id']})"]
        if c["location_error"] is not None:
            lines.append(f"  location: ESI refused ({c['location_error']})")
        if c["location_hint"] is not None:
            lines.append("  " + c["location_hint"])
        if c["clones_error"] is not None:
            lines.append(f"  clones: ESI refused ({c['clones_error']})")
        current = c["current"]
        if current:
            where, system = current["where"], current["system"]
            lines.append(f"  current: {where}" + (f" ({system})" if where not in ("-", system) else ""))
        if c["home"]:
            lines.append(f"  home: {c['home']}")
        if c["last_clone_jump"]:
            lines.append(f"  last clone jump: {render.parse_ts(c['last_clone_jump']).strftime('%Y-%m-%d %H:%M')} UTC")
        if c["jump_clones"]:
            rows = [[jc["name"] or "(unnamed)", jc["location"], ", ".join(jc["implants"]) or "-"]
                    for jc in sorted(c["jump_clones"], key=lambda j: j["name"] or "")]
            lines.append(render.table(["jump clone", "location", "implants"], rows))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def travel_csv(data: dict, args) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["character", "kind", "location", "clone_name", "implants"])
    for c in data["characters"]:
        current = c["current"]
        if current:
            writer.writerow([c["name"], "current", current["where"] if current["where"] != "-" else current["system"], "", ""])
        if c["home"]:
            writer.writerow([c["name"], "home", c["home"], "", ""])
        for jc in c["jump_clones"]:
            writer.writerow([c["name"], "jump clone", jc["location"], jc["name"] or "(unnamed)", ", ".join(jc["implants"])])
    return buf.getvalue()


# -- implants -------------------------------------------------------------------------------------

def implants_data(args, ctx=None) -> dict:
    client, chars, hints = targets(args, [("clones", "esi-clones.read_implants.v1")])
    characters = []
    for tok, public in chars:
        cid = tok["character_id"]
        doc = client.get(f"/characters/{cid}/implants", token=tok["access_token"])
        ids = doc.get("implants") if isinstance(doc, dict) else doc
        ids = [int(i) for i in (ids or [])]
        names = esi_mod.resolve_names(client, set(ids)) if ids else {}
        # one entry per implant instance: the same type can be fitted in both head slots
        characters.append({"name": public.get("name") or str(cid), "id": cid,
                           "implants": [names.get(i, f"id {i}") for i in sorted(ids, key=lambda i: names.get(i, ""))]})
    return {"hints": list(hints), "characters": characters}


def implants_text(data: dict, args) -> str:
    blocks = list(data["hints"])
    for c in data["characters"]:
        table = render.table(["implant"], [[name] for name in c["implants"]]) if c["implants"] else "(no implants fitted)"
        blocks.append(f"{c['name']} (id {c['id']})\n{table}")
    return "\n\n".join(blocks)


def implants_csv(data: dict, args) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["character", "implant"])
    for c in data["characters"]:
        writer.writerows([c["name"], name] for name in c["implants"])
    return buf.getvalue()
