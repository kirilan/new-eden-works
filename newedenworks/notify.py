"""Alerts to a Telegram chat: the core's notify service, fed by the plugins' alert sources.

One `alerts run` (the service's `alerts` job, every 20 minutes) asks each loaded `AlertSource` for
its part of the run (trade: market orders; industry: jobs ending and the finish-window summary), and
the core owns everything they share:

- the channel: the bot and chat (`telegram.json`), `alerts setup` and `alerts test`;
- the settings: config.json's `notify` section (quiet hours, time zone, reminder interval, excluded
  characters and locations), and each source's keys in its plugin's section (`industry`: `job_alerts`,
  `job_notice_minutes`, `jobs`; `trade.alerts`: `min_gap_pct`). Until config.json has a `notify`
  section, an `alerts.json` in the config directory is read instead, as before (`neworks migrate`
  splits it into the sections);
- quiet hours: news during them is sent silently;
- the reminder: items announced earlier and still standing (an order still behind, a job not yet
  delivered) are rolled up on one clock, at most once per `remind_hours` and never in quiet hours.
  That clock (`notify.json` in the state directory) is the core's; each source keeps its own state;
- the one message per run: a title with every source's counts, then the sources' blocks, each at the
  place its rank gives it;
- saving: every state is saved only after Telegram took the message, so a failed send is retried.

A source returns a `Batch` (`collect(run)`): what is fresh, which announced items stand and since
when, and `render(reminder)`, the `Parts` it adds once the core knows whether the reminder goes out.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from . import exports, localtime, market, paths, sso, storage, telegram
from .plugin import Command

SETTINGS_NAME = "alerts.json"      # the settings before config.json's sections; read while there is no `notify`
# Where each setting lives in config.json: the core's `notify` section, or the source's plugin section.
NOTIFY_KEYS = ("exclude_locations", "exclude_characters", "remind_hours", "quiet_hours", "timezone")
INDUSTRY_KEYS = ("job_alerts", "job_notice_minutes", "jobs")
TRADE_KEYS = ("min_gap_pct",)       # under trade.alerts
STATE_NAME = "notify.json"
# The order sources are polled in, and their parts and log pieces follow; one this list does not
# name comes after, in plugin load order.
SOURCE_ORDER = ("orders", "jobs")

DEFAULT_SETTINGS = {
    "exclude_locations": [],       # case-insensitive substrings of the station/structure name
    "exclude_characters": [],      # character names
    "remind_hours": 4,
    # A sell order less than this many % above the price a reprice would take is not reported as undercut
    # (it keeps selling; `orders --check` reads it `leave`). 0 = report every undercut.
    "min_gap_pct": 0,
    "quiet_hours": "00:00-08:00",  # local time below; empty string = never quiet
    "timezone": "UTC",
    "job_alerts": True,            # industry jobs ending soon, then in the reminder until delivered
    "job_notice_minutes": 20,
    # On top of the per-job alerts (`job_alerts.py`): a summary at the start of each finish window, and
    # job news held back while more of the same site is about to finish. `windows: null` = the operation
    # file's planning.finish_windows (none there: no summaries).
    "jobs": {"enabled": True, "windows": None, "batch_minutes": 45, "max_wait_minutes": 90},
}
JOB_DEFAULTS = DEFAULT_SETTINGS["jobs"]


@dataclass(frozen=True)
class Settings:
    exclude_locations: tuple[str, ...] = ()
    exclude_characters: tuple[str, ...] = ()
    remind_hours: float = 4
    quiet_start: dtime | None = None
    quiet_end: dtime | None = None
    timezone: str = "UTC"
    job_alerts: bool = True
    job_notice_minutes: float = 20
    jobs_enabled: bool = True             # window summary and batching (job_alerts.py)
    jobs_windows: str | None = None       # None: the operation file's planning.finish_windows
    batch_minutes: float = 45
    max_wait_minutes: float = 90
    min_gap_pct: float = 0

    def close_behind(self, row: dict) -> bool:
        """An undercut sell order still within `min_gap_pct` of the price a reprice would take."""
        gap = row.get("gap_pct")
        return (self.min_gap_pct > 0 and row.get("status") == "undercut" and gap is not None
                and gap < self.min_gap_pct)

    def excluded_character(self, name) -> bool:
        return str(name or "").casefold() in {n.casefold() for n in self.exclude_characters}

    def excluded(self, row: dict) -> bool:
        where = str(row.get("location") or "").casefold()
        who = str(row.get("owner") or "").casefold()
        return (any(part.casefold() in where for part in self.exclude_locations if part)
                or who in {name.casefold() for name in self.exclude_characters})

    def quiet(self, now: datetime) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        local = now.astimezone(localtime.zone(self.timezone)).time()
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= local < self.quiet_end
        return local >= self.quiet_start or local < self.quiet_end     # wraps midnight


def _clock(text: str) -> dtime:
    hours, _, minutes = text.strip().partition(":")
    return dtime(int(hours), int(minutes or 0))


def setting_name(key: str, legacy: bool = True) -> str:
    """Where a setting is written, for an error message: `alerts.json: jobs.windows` or
    `config.json: industry.jobs.windows`."""
    if legacy:
        return f"alerts.json: {key}"
    top = key.split(".")[0]
    section = "notify" if top in NOTIFY_KEYS else "industry" if top in INDUSTRY_KEYS else "trade.alerts"
    return f"config.json: {section}.{key}"


def parse_settings(doc: dict, legacy: bool = True) -> Settings:
    """Settings from one flat document (alerts.json, or config.json's sections through `sections_doc`);
    a malformed value is an error that names it."""
    def name(key: str) -> str:
        return setting_name(key, legacy)

    merged = {**DEFAULT_SETTINGS, **(doc or {})}
    quiet = str(merged.get("quiet_hours") or "").strip()
    start = end = None
    if quiet:
        try:
            first, second = quiet.split("-")
            start, end = _clock(first), _clock(second)
        except ValueError:
            raise RuntimeError(f"{name('quiet_hours')} {quiet!r} is not HH:MM-HH:MM") from None
    try:
        remind = float(merged.get("remind_hours") or 0)
    except (TypeError, ValueError):
        raise RuntimeError(f"{name('remind_hours')} must be a number (0 = no reminders)") from None
    try:
        notice = float(merged.get("job_notice_minutes") or 0)
    except (TypeError, ValueError):
        raise RuntimeError(f"{name('job_notice_minutes')} must be a number") from None
    try:
        min_gap = float(merged.get("min_gap_pct") or 0)
    except (TypeError, ValueError):
        raise RuntimeError(f"{name('min_gap_pct')} must be a number (0 = report every undercut)") from None
    if min_gap < 0:
        raise RuntimeError(f"{name('min_gap_pct')} must be 0 or more")
    zone_name = str(merged.get("timezone") or "UTC")
    try:
        localtime.zone(zone_name)       # an IANA name, or a fixed offset such as +02:00
    except RuntimeError as err:
        raise RuntimeError(f"{name('timezone')}: {err}") from None
    jobs = merged.get("jobs") if merged.get("jobs") is not None else {}
    if not isinstance(jobs, dict):
        raise RuntimeError(f"{name('jobs')} must be an object such as {{\"enabled\": true}}")
    unknown = sorted(set(jobs) - set(JOB_DEFAULTS))
    if unknown:
        raise RuntimeError(f"{name('jobs')}: unknown key {unknown[0]!r} (known: {', '.join(JOB_DEFAULTS)})")
    jobs = {**JOB_DEFAULTS, **jobs}
    windows = jobs.get("windows")
    if windows is not None:
        if not isinstance(windows, str):
            raise RuntimeError(f"{name('jobs.windows')} must be a string such as \"08-10,20-22\" (or null)")
        windows = windows.strip()
        if windows:
            try:
                localtime.parse_windows(windows)
            except RuntimeError as err:
                raise RuntimeError(f"{name('jobs.windows')}: {str(err).replace('--finish-window ', '')}") from None
    minutes = {}
    for key in ("batch_minutes", "max_wait_minutes"):
        try:
            minutes[key] = float(jobs.get(key) or 0)
        except (TypeError, ValueError):
            raise RuntimeError(f"{name('jobs.' + key)} must be a number of minutes") from None
    return Settings(tuple(str(x) for x in merged.get("exclude_locations") or []),
                    tuple(str(x) for x in merged.get("exclude_characters") or []),
                    remind, start, end, zone_name,
                    bool(merged.get("job_alerts")), notice,
                    bool(jobs.get("enabled")), windows, minutes["batch_minutes"], minutes["max_wait_minutes"],
                    min_gap)


def settings_path() -> str:
    """alerts.json, the settings file before config.json's sections."""
    return os.path.join(paths.config_dir(create=False), SETTINGS_NAME)


def legacy_in_use(cfg: dict) -> bool:
    """alerts.json is read only while config.json has no `notify` section."""
    return "notify" not in cfg and os.path.exists(settings_path())


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def sections_doc(cfg: dict) -> dict:
    """The settings config.json's sections hold, as the flat document `parse_settings` reads."""
    notify, industry = _object(cfg.get("notify")), _object(cfg.get("industry"))
    trade = _object(_object(cfg.get("trade")).get("alerts"))
    return {**{k: notify[k] for k in NOTIFY_KEYS if k in notify},
            **{k: industry[k] for k in INDUSTRY_KEYS if k in industry},
            **{k: trade[k] for k in TRADE_KEYS if k in trade}}


def split_doc(doc: dict) -> tuple[dict, dict, dict, list[str]]:
    """An alerts.json document as its config.json sections - notify, industry, trade.alerts - and the
    keys none of them takes (which `parse_settings` never read either)."""
    notify = {k: doc[k] for k in NOTIFY_KEYS if k in doc}
    industry = {k: doc[k] for k in INDUSTRY_KEYS if k in doc}
    trade = {k: doc[k] for k in TRADE_KEYS if k in doc}
    return notify, industry, trade, sorted(set(doc) - set(NOTIFY_KEYS) - set(INDUSTRY_KEYS) - set(TRADE_KEYS))


def read_legacy(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as err:
        raise RuntimeError(f"{path}: not valid JSON ({err})") from None
    return doc if isinstance(doc, dict) else {}


def load_settings(cfg: dict | None = None) -> Settings:
    """config.json's `notify` section and the sources' sections, or alerts.json while there is no
    `notify` section; the defaults for anything neither sets."""
    cfg = sso.load_config() if cfg is None else cfg
    if legacy_in_use(cfg):
        return parse_settings(read_legacy(settings_path()))
    return parse_settings(sections_doc(cfg), legacy=False)


def where_settings(cfg: dict | None = None) -> str:
    """Where the settings are read from, for `alerts setup`."""
    cfg = sso.load_config() if cfg is None else cfg
    if legacy_in_use(cfg):
        return settings_path()
    return (f"{sso.config_file(create=False)}: notify (quiet hours, time zone, reminder, exclusions), "
            f"industry (job_alerts, job_notice_minutes, jobs), trade.alerts (min_gap_pct)")


def problems(cfg: dict) -> list[tuple[str, str]]:
    """What is wrong with the alert settings in config.json, as (section, message) pairs for `doctor`."""
    found = []
    if cfg.get("notify") is not None and not isinstance(cfg.get("notify"), dict):
        found.append(("notify", "notify must be an object"))
    trade_alerts = _object(cfg.get("trade")).get("alerts")
    if trade_alerts is not None and not isinstance(trade_alerts, dict):
        found.append(("trade", "trade.alerts must be an object"))
    unknown = sorted(set(_object(cfg.get("notify"))) - set(NOTIFY_KEYS))
    if unknown:
        found.append(("notify", f"notify: unknown key {unknown[0]!r} (known: {', '.join(NOTIFY_KEYS)})"))
    if legacy_in_use(cfg):
        return found
    try:
        parse_settings(sections_doc(cfg), legacy=False)
    except RuntimeError as exc:
        text = str(exc)
        owner = text.split(": ", 1)[1].split(".")[0] if text.startswith("config.json: ") else "notify"
        found.append((owner, text))
    if "notify" in cfg and os.path.exists(settings_path()):
        found.append(("notify", f"{settings_path()} is no longer read (config.json has a notify section): "
                                "delete it, or rename it to keep it"))
    return found




# -- shared wording --------------------------------------------------------------------------------

_NUMERAL = re.compile(r"\s+[IVXLC]+$")


def where(row: dict) -> str:
    """The system part of a station or structure name - `Jita`, `X-EXMPL` - which is how one says where.

    A station is `System Planet - Moon - Name`, a structure `System - Name`: the first part without the
    planet's numeral. A place nobody could name (`facility 123`) stays whole; no name at all is empty."""
    name = str(row.get("location") or "")
    return _NUMERAL.sub("", name.split(" - ")[0].strip())


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


# -- the core's state: the reminder clock ----------------------------------------------------------

def state_path() -> str:
    return os.path.join(paths.state_dir(), STATE_NAME)


def load_state() -> dict:
    """The reminder clock. Before notify was the core's it lived in the order alerts' state, where the
    first run on this code reads it from."""
    for path, key in ((state_path(), "last_reminder"),
                      (os.path.join(paths.state_dir(), "order-alerts.json"), "last_reminder")):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return {"last_reminder": doc.get(key)}
    return {"last_reminder": None}


def save_state(state: dict) -> None:
    storage.atomic_write_json(state_path(), state)


# -- what a source hands the core ------------------------------------------------------------------

@dataclass
class Parts:
    """A source's part of the message once the reminder is decided.

    `content`: whether it adds to the titled message (`EVE <label>: ...`, or `EVE: ...` when
    several sources do). `heads` and `blocks` are (rank, text): the title's pieces and the message's
    blocks, merged across sources by rank (stable within a source). `after` are whole messages of
    their own appended after it (the window summary). `counts` and `log_tail` feed the run's log line."""

    label: str
    content: bool = False
    heads: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    after: list = field(default_factory=list)
    counts: str | None = None
    log_tail: str = ""


@dataclass
class Batch:
    """A source's run before the reminder is decided.

    `fresh`: news that starts a reminder interval (an order newly behind, a job just ending).
    `standing`: when each announced, still-standing item was announced (epoch seconds); they go out
    together when the reminder is due. `render(reminder) -> Parts`. `save()` stores the source's
    state; it is called only after the message was sent. `warnings` print as `warning: ...` before
    the run decides anything, `notes` only with --dry-run. `log_head` opens the log line."""

    render: Callable[[bool], Parts]
    save: Callable[[], None] = lambda: None
    fresh: bool = False
    standing: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    log_head: str | None = None


@dataclass
class AlertRun:
    """What every source of one run shares: the settings, the clock, ESI and the stored characters."""

    settings: Any
    now: datetime
    client: Any
    chars: list
    dry_run: bool = False


def reminder_due(state: dict, batches: list[Batch], settings, now: datetime) -> tuple[dict, bool]:
    """(new state, whether the standing items go out now). One clock for every source: from the last
    reminder, or else from the oldest announcement still standing; nothing standing and nothing fresh
    resets it, so the next news starts a fresh interval."""
    ts = now.timestamp()
    last = state.get("last_reminder")
    standing = [float(t) for b in batches for t in b.standing]
    reminder = False
    if standing:
        anchor = last if last is not None else min(standing)
        due = settings.remind_hours > 0 and ts - float(anchor) >= settings.remind_hours * 3600
        if due and not settings.quiet(now):
            last, reminder = ts, True
    elif not any(b.fresh for b in batches):
        last = None
    return {"last_reminder": last}, reminder


def compose(parts: list[Parts]) -> str | None:
    """The run's message: the titled part from every source with content, then their own messages."""
    with_content = [p for p in parts if p.content]
    main = None
    if with_content:
        title = f"EVE {with_content[0].label}" if len(with_content) == 1 else "EVE"
        heads = [text for _, text in sorted((h for p in with_content for h in p.heads), key=lambda h: h[0])]
        blocks = [text for _, text in sorted((b for p in with_content for b in p.blocks), key=lambda b: b[0])]
        main = "\n\n".join([f"{title}: " + ", ".join(heads)] + blocks)
    return "\n\n".join(p for p in [main, *(a for part in parts for a in part.after)] if p) or None


def log_line(stamp: str, batches: list[Batch], parts: list[Parts], text: str | None, silent: bool) -> str:
    head = "; ".join(b.log_head for b in batches if b.log_head)
    body = ("sent: " + ", ".join(p.counts for p in parts if p.counts) + (" (silent)" if silent else "")
            if text else "nothing new")
    return f"{stamp} " + (f"{head}; " if head else "") + body + "".join(p.log_tail for p in parts)


# -- the sources -----------------------------------------------------------------------------------

def sources(registry) -> list:
    found = {source.name: source for source, _plugin in registry.alert_sources()}
    names = [n for n in SOURCE_ORDER if n in found] + [n for n in found if n not in SOURCE_ORDER]
    return [found[n] for n in names]


# -- commands --------------------------------------------------------------------------------------

def _read_token(path: str | None) -> str | None:
    """The bot token from a file, or stdin for `-`: never a command-line argument, which `ps` shows."""
    if not path:
        return None
    with (contextlib.nullcontext(sys.stdin) if path == "-" else open(path, encoding="utf-8")) as fh:
        token = fh.read().strip()
    if not token:
        raise RuntimeError("the token file is empty")
    return token


def _configured(need_chat: bool = True) -> dict:
    doc = telegram.load_config()
    if not doc.get("token"):
        raise RuntimeError("no Telegram bot yet - run: neworks alerts setup --token-file FILE")
    if need_chat and not doc.get("chat_id"):
        raise RuntimeError("the bot has no chat yet - send it any message in Telegram, then run: "
                           "neworks alerts setup")
    return doc


def cmd_setup(args) -> int:
    doc = telegram.load_config()
    token = _read_token(args.token_file) or doc.get("token")
    if not token:
        raise RuntimeError("pass --token-file FILE (or - for stdin) with the token @BotFather gave you")
    name = telegram.bot_name(token)
    doc["token"] = token
    if args.chat_id is not None:
        doc["chat_id"] = args.chat_id
    else:
        chats = telegram.private_chats(token)
        if not chats:
            telegram.save_config(doc)
            print(f"token saved for @{name}; no chat yet - open @{name} in Telegram, send it any "
                  f"message (e.g. /start) and run this again", file=sys.stderr)
            return 1
        if len(chats) > 1:
            listing = ", ".join(f"{c['id']} ({c['name']})" for c in chats)
            telegram.save_config(doc)
            print(f"several chats wrote to @{name}: {listing}; pick one with --chat-id", file=sys.stderr)
            return 1
        doc["chat_id"] = chats[0]["id"]
    telegram.save_config(doc)
    telegram.send(token, doc["chat_id"], "New Eden Works: alerts will arrive in this chat.")
    print(f"@{name} will send alerts to chat {doc['chat_id']}; saved in {telegram.config_path()}")
    raw = sso._load_json(sso.config_file(create=False))
    if not legacy_in_use(raw) and "notify" not in raw:
        # Something to edit: the notify section with its defaults (the sources' keys keep theirs).
        sso.save_config({"notify": {k: DEFAULT_SETTINGS[k] for k in NOTIFY_KEYS}})
    print(f"alert settings: {where_settings()}")
    return 0


def cmd_test(args) -> int:
    doc = _configured()
    telegram.send(doc["token"], doc["chat_id"], args.text or "New Eden Works: test message")
    print("sent")
    return 0


def run(registry, args) -> int:
    """One check: every source collects, the core decides the reminder, composes, sends and saves."""
    settings = load_settings()
    doc = None if args.dry_run else _configured()
    chosen = sources(registry)
    features = [f for source in chosen for f in (source.features(settings) if source.features else [])]
    client, chars, hints = exports.targets(SimpleNamespace(char=None, csv=False), features)
    for line in hints:
        print(line, file=sys.stderr)
    now = datetime.fromtimestamp(client.now().timestamp(), timezone.utc)
    alert_run = AlertRun(settings, now, client, chars, dry_run=args.dry_run)
    batches = [source.collect(alert_run) for source in chosen]
    for batch in batches:
        for line in batch.warnings:
            print(f"warning: {line}", file=sys.stderr)
    state, reminder = reminder_due(load_state(), batches, settings, now)
    parts = [batch.render(reminder) for batch in batches]
    text = compose(parts)
    silent = settings.quiet(now)
    if args.dry_run:
        print(text or "(nothing new)")
        for batch in batches:
            for line in batch.notes:
                print(line, file=sys.stderr)
        return 0
    if text:
        telegram.send(doc["token"], doc["chat_id"], text, silent=silent)
    for batch in batches:
        batch.save()
    save_state(state)
    if not args.quiet:
        print(log_line(market.iso_utc(now.timestamp()), batches, parts, text, silent))
    return 0


def cmd_alerts(args) -> int:
    action = getattr(args, "alerts_action", None)
    if action == "setup":
        return cmd_setup(args)
    if action == "test":
        return cmd_test(args)
    if action == "run":
        from . import registry as registry_mod
        return run(registry_mod.current(), args)
    raise RuntimeError("usage: neworks alerts {setup,test,run}")


def _alerts_args(p_alerts: argparse.ArgumentParser) -> None:
    alerts_sub = p_alerts.add_subparsers(dest="alerts_action")
    p_asetup = alerts_sub.add_parser("setup", help="store the bot token (from @BotFather) and find the chat; "
                                                    "message the bot in Telegram first")
    p_asetup.add_argument("--token-file", metavar="FILE",
                          help="file holding the bot token, or - for stdin (default: keep the stored token)")
    p_asetup.add_argument("--chat-id", type=int, help="send to this chat instead of the one that messaged the bot")
    p_atest = alerts_sub.add_parser("test", help="send a test message")
    p_atest.add_argument("text", nargs="?", help="message text")
    p_arun = alerts_sub.add_parser("run", help="check every open order once and send what is new "
                                               "(for a timer: contrib/systemd/eve-orders-alert.timer)")
    p_arun.add_argument("--dry-run", action="store_true", help="print the message; send nothing, save no state")
    p_arun.add_argument("--quiet", action="store_true", help="no summary line on stdout")


COMMAND = Command(("alerts",), help=("Telegram alerts for undercut/outbid, back-on-top and filled market orders, and "
                                     "industry jobs about to finish"),
                  configure=_alerts_args, handler=cmd_alerts)
