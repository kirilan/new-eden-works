"""Scheduled jobs: `neworks run JOB` runs one, `neworks service` runs them all on their schedules.

Every plugin's `Job`s are run by one long-running process (a single systemd user unit) instead of a
timer per job. The service starts each run as a subprocess, `python -m newedenworks run JOB
--scheduled`, so a job that fails or hangs never takes another one with it, and the run itself:

- takes the job's lock (`<state dir>/service/JOB.lock`), so two runs of one job never overlap,
  whoever started them: the service, a systemd timer, or someone at a terminal;
- with `--scheduled`, skips when the job already started in the current slot (`Job.every`). This is
  what lets the service run side by side with the old timers: whichever reaches a slot first runs
  the job, and the other one finds it done and says so;
- records when it started and finished and its exit code in `<state dir>/service/JOB.json`, which
  `neworks service status` reads.

Slots are counted in UTC from the epoch: a 20m job is due at :00, :20 and :40, a 1h job on the hour,
a 1d job at 00:00 UTC.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from . import paths, render, storage
from .plugin import Command, Context, Job, PluginError

DAEMON = "daemon"
RESTART_SECONDS = 30            # a daemon job that fails is started again after this long (systemd's RestartSec)
STOP_SECONDS = 10               # how long children get to exit after the service is told to stop
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def seconds(text: str) -> int:
    """"20m" -> 1200. Empty means 0. Raises ValueError on anything else."""
    if text in ("", "0"):
        return 0
    match = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not match or int(match.group(1)) == 0:
        raise ValueError(f"not a period: {text!r} (use a number and s, m, h or d, such as 20m)")
    return int(match.group(1)) * _UNITS[match.group(2)]


def validate(job: Job) -> None:
    """Refuse a job whose name or schedule the service could not use (PluginError)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", job.name or ""):
        raise PluginError(f"job name {job.name!r} must be lower-case words joined by hyphens")
    try:
        if job.every != DAEMON and seconds(job.every) == 0:
            raise ValueError(f"every must be a period or {DAEMON!r}, not {job.every!r}")
        seconds(job.jitter)
    except ValueError as exc:
        raise PluginError(f"job {job.name}: {exc}") from None


def slot_start(now: float, every: int) -> float:
    return now - (now % every)


def command(*argv: str) -> Callable[[Context], int]:
    """A job's `run` that runs one CLI command line in this process, as typed after `neworks`."""
    def run(ctx: Context) -> int:
        from . import cli
        return cli.main(list(argv))
    run.argv = argv                       # shown by `service status`
    return run


# -- state -----------------------------------------------------------------------------------------

def _dir(create: bool) -> str:
    path = os.path.join(paths.state_dir(create=create), "service")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def state_path(name: str, create: bool = False) -> str:
    return os.path.join(_dir(create), f"{name}.json")


def load_state(name: str) -> dict:
    try:
        with open(state_path(name), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(name: str, data: dict) -> None:
    storage.atomic_write_json(state_path(name, create=True), data)


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


# -- which jobs ------------------------------------------------------------------------------------

def enabled(job: Job, cfg: dict | None = None) -> bool:
    """config.json's `service.jobs.NAME` when it is true or false, otherwise the job's default."""
    from . import config
    setting = config.service_jobs(cfg).get(job.name)
    return setting if isinstance(setting, bool) else job.default


# -- one run ---------------------------------------------------------------------------------------

def run_job(registry, name: str, scheduled: bool = False, now: Callable[[], float] = time.time) -> int:
    """Run one job in this process under its lock; the exit code is the job's."""
    entry = registry.job(name)
    if entry is None:
        known = ", ".join(job.name for job, _ in registry.jobs()) or "none"
        print(f"error: no job named {name} (jobs: {known})", file=sys.stderr)
        return 2
    job, plugin = entry
    with storage.file_lock(os.path.join(_dir(True), f"{name}.lock")):
        state = load_state(name)
        started = now()
        if scheduled and job.every != DAEMON:
            slot = slot_start(started, seconds(job.every))
            last = _ts(state.get("slot"))
            if last is not None and last >= slot:
                print(f"{name}: already ran in this slot (started {state.get('last_start')}); skipped")
                return 0
        if job.every != DAEMON:
            state["slot"] = _iso(slot_start(started, seconds(job.every)))
        state.update(last_start=_iso(started), last_end=None, last_rc=None,
                     runner="scheduled" if scheduled else "manual")
        _save_state(name, state)
        try:
            rc = int(job.run(Context(registry, plugin)) or 0)
        except SystemExit as exc:          # argparse and friends
            rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except KeyboardInterrupt:
            rc = 130
        except Exception:                  # noqa: BLE001 - a job's failure is its exit code
            traceback.print_exc()
            rc = 1
        state.update(last_end=_iso(now()), last_rc=rc)
        _save_state(name, state)
        return rc


# -- the service loop ------------------------------------------------------------------------------

@dataclass
class _Slot:
    job: Job
    due: float
    proc: Any = None


def _log(message: str, clock: Callable[[], float] = time.time) -> None:
    print(f"{_iso(clock())} {message}", flush=True)


def _next_due(job: Job, after: float, rand: Callable[[float, float], float]) -> float:
    every = seconds(job.every)
    return slot_start(after, every) + every + rand(0, seconds(job.jitter))


def first_due(job: Job, now: float, rand: Callable[[float, float], float] = random.uniform) -> float:
    """When a periodic job first runs after the service starts: now when its last slot was missed."""
    if job.every == DAEMON:
        return now
    last = _ts(load_state(job.name).get("slot"))
    if last is None or last < slot_start(now, seconds(job.every)):
        return now
    return _next_due(job, now, rand)


def spawn_argv(name: str) -> list[str]:
    return [sys.executable, "-m", "newedenworks", "run", name, "--scheduled"]


def serve(registry, cfg: dict | None = None, *, clock: Callable[[], float] = time.time,
          sleep: Callable[[float], None] = time.sleep, popen=subprocess.Popen,
          rand: Callable[[float, float], float] = random.uniform, stop: Callable[[], bool] | None = None) -> int:
    """Run every enabled job on its schedule until stopped (SIGTERM, SIGINT, or `stop()` true)."""
    jobs = [job for job, _ in registry.jobs() if enabled(job, cfg)]
    if not jobs:
        print("no jobs to run: every job is turned off (config.json service.jobs)", file=sys.stderr)
        return 1
    stopping = {"flag": False}

    def _stop(signum, frame):
        stopping["flag"] = True

    if stop is None:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        stop = lambda: stopping["flag"]     # noqa: E731

    now = clock()
    slots = [_Slot(job, first_due(job, now, rand)) for job in jobs]
    _log("service started: " + ", ".join(f"{s.job.name} ({s.job.every})" for s in slots), clock)
    while not stop():
        now = clock()
        for s in slots:
            if s.proc is not None:
                rc = s.proc.poll()
                if rc is None:
                    continue
                s.proc = None
                _log(f"{s.job.name} finished, exit {rc}", clock)
                if s.job.every == DAEMON:
                    if rc == 0:
                        s.due = float("inf")
                        _log(f"{s.job.name} exited cleanly; not restarted", clock)
                    else:
                        s.due = now + RESTART_SECONDS
            if s.proc is None and s.due <= now:
                _log(f"{s.job.name} started", clock)
                s.proc = popen(spawn_argv(s.job.name))
                if s.job.every != DAEMON:
                    s.due = _next_due(s.job, now, rand)
        waits = [s.due - now for s in slots if s.proc is None]
        sleep(max(1.0, min([5.0, *waits])))
    running = [s for s in slots if s.proc is not None]
    for s in running:
        s.proc.terminate()
    deadline = clock() + STOP_SECONDS
    for s in running:
        try:
            s.proc.wait(timeout=max(0.0, deadline - clock()))
        except subprocess.TimeoutExpired:
            s.proc.kill()
    _log("service stopped", clock)
    return 0


# -- commands --------------------------------------------------------------------------------------

def status_data(args, ctx) -> dict:
    registry = ctx.registry
    now = time.time()
    rows = []
    for job, plugin in registry.jobs():
        state = load_state(job.name)
        on = enabled(job)
        next_due = None
        if on and job.every != DAEMON:
            last = _ts(state.get("slot"))
            every = seconds(job.every)
            next_due = _iso(now if last is None or last < slot_start(now, every)
                            else slot_start(now, every) + every)
        rows.append({
            "job": job.name,
            "plugin": plugin,
            "every": job.every,
            "jitter": job.jitter or None,
            "enabled": on,
            "help": job.help,
            "command": list(getattr(job.run, "argv", ()) or ()) or None,
            "last_start": state.get("last_start"),
            "last_end": state.get("last_end"),
            "last_rc": state.get("last_rc"),
            "runner": state.get("runner"),
            "next_due": next_due,
        })
    return {"jobs": rows}


def status_text(data: dict, args) -> str:
    if not data["jobs"]:
        return "no plugin declares a job"
    rows = []
    for j in data["jobs"]:
        last = j["last_start"] or "never"
        if j["last_start"] and j["last_end"] is None:
            last += " (running or cut off)"
        elif j["last_rc"] not in (None, 0):
            last += f" (exit {j['last_rc']})"
        rows.append([j["job"], j["plugin"], j["every"], "on" if j["enabled"] else "off", last,
                     j["next_due"] or "-", " ".join(j["command"] or []) or j["help"]])
    return render.table(["job", "plugin", "every", "state", "last run", "next due", "runs"], rows)


UNIT_TEMPLATE = """\
# New Eden Works service: runs every enabled plugin's jobs (see `neworks service status`).
# Install: save as ~/.config/systemd/user/new-eden-works.service, then
#   systemctl --user daemon-reload && systemctl --user enable --now new-eden-works.service
[Unit]
Description=New Eden Works: scheduled jobs (alerts, price sync) and the dashboard
After=network-online.target

[Service]
Type=simple
WorkingDirectory={cwd}
ExecStart={python} -m newedenworks service
Restart=on-failure
RestartSec=30
Nice=10

[Install]
WantedBy=default.target
"""


def unit_text() -> str:
    return UNIT_TEMPLATE.format(cwd=os.getcwd(), python=sys.executable)


def _service_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", nargs="?", choices=("run", "status", "unit"), default="run",
                   help="run (default): run every enabled job on its schedule until stopped; status: each "
                        "job's schedule, last run and next slot; unit: print a systemd user unit for the service")
    p.add_argument("--json", action="store_true", help="machine-readable status")


def cmd_service(args) -> int:
    from . import registry as registry_mod
    registry = registry_mod.current()
    if args.action == "unit":
        sys.stdout.write(unit_text())
        return 0
    if args.action == "status":
        data = status_data(args, Context(registry))
        print(json.dumps(data, indent=2) if args.json else status_text(data, args))
        return 0
    return serve(registry)


def _run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("job", help="the job's name (neworks service status lists them)")
    p.add_argument("--scheduled", action="store_true",
                   help="skip when the job already started in its current slot: how the service and the "
                        "systemd timers run it, so that running both never runs a slot twice")


def cmd_run(args) -> int:
    from . import registry as registry_mod
    return run_job(registry_mod.current(), args.job, scheduled=args.scheduled)


COMMANDS = (
    Command(("service",), help="run every plugin's scheduled jobs (alerts, price sync, dashboard) in one process",
            configure=_service_args, handler=cmd_service),
    Command(("run",), help="run one scheduled job now, under its lock (see service status for the jobs)",
            configure=_run_args, handler=cmd_run),
)
