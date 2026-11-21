"""The dashboard plugin: a local web page built from every loaded plugin's panels.

`serve` runs a small HTTP server (standard library only) that rebuilds its document every
`--refresh` minutes: a ledger sync when the last one is that old (the ledger's `sync_if_due` hook),
then every panel on the configured pages (`newedenworks.panels`): the ledger's replay, and the live
reports the panels read (`industry status`, `orders --check`, `colonies --detail`, `ledger
inventory`). In between, every `--orders-refresh` minutes, only the quick panels (trade's orders) are
re-read. A panel that fails keeps its last good part and says why. The page polls the document and
redraws; config.json's `dashboard.pages` chooses the panels per page.
`build` writes one document as JSON and exits - the same data, for a script or a cron job.

The server is read-only: it answers GET for the page and the document, and POST /api/refresh only
asks the refresh thread to run early (at most once per `MIN_MANUAL_REFRESH_SECONDS`). Nothing it serves
needs a login, so bind it to a network you trust.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import __version__, esi as esi_mod, panels, paths, sso, storage
from .plugin import API_VERSION, Command, ConfigSpec, Job, Plugin
from .service import DAEMON

DEFAULT_PORT = 8765
DEFAULT_REFRESH_MINUTES = 60
# ESI caches a character's order book for 20 minutes, so between full rebuilds the orders panel is
# re-read on that cadence: an hourly rebuild alone showed reprices up to 80 minutes late.
DEFAULT_ORDERS_REFRESH_MINUTES = 20
MIN_MANUAL_REFRESH_SECONDS = 300


# config.json's `dashboard` section as `dashboard serve` options: what the service's dashboard job runs.
SERVE_SETTINGS = {"host": "--host", "port": "--port", "refresh_minutes": "--refresh",
                  "orders_refresh_minutes": "--orders-refresh", "since": "--since", "orders_char": "--orders-char"}
SERVE_FLAGS = {"no_sync": "--no-sync", "no_live": "--no-live", "verbose": "--verbose"}


def serve_argv(settings: dict | None) -> list[str]:
    """`dashboard serve` with the `dashboard` section's settings; RuntimeError names a wrong one."""
    settings = settings or {}
    if not isinstance(settings, dict):
        raise RuntimeError("config.json's dashboard section must be an object")
    argv = ["dashboard", "serve"]
    for key, value in settings.items():
        if key in SERVE_SETTINGS:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise RuntimeError(f"dashboard.{key} must be a string or a number")
            argv += [SERVE_SETTINGS[key], str(value)]
        elif key == "pages":
            continue                    # the layout, read by the server itself
        elif key in SERVE_FLAGS:
            if not isinstance(value, bool):
                raise RuntimeError(f"dashboard.{key} must be true or false")
            if value:
                argv.append(SERVE_FLAGS[key])
        else:
            raise RuntimeError(f"dashboard.{key} is not a dashboard setting (known: "
                               + ", ".join([*SERVE_SETTINGS, *SERVE_FLAGS, "pages"]) + ")")
    return argv


def serve_job(ctx) -> int:
    """The service's dashboard job: `dashboard serve` as config.json's `dashboard` section says."""
    from . import cli, sso
    cfg = sso.load_config()
    return cli.main(serve_argv(cfg.get("dashboard") if isinstance(cfg, dict) else None))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(message: str) -> None:
    print(f"{_iso(_now())} dashboard: {message}", file=sys.stderr, flush=True)


def _layout(registry) -> tuple[list[dict], list[str]]:
    from . import sso
    cfg = sso.load_config()
    return panels.layout(registry, cfg.get("dashboard") if isinstance(cfg, dict) else None)


def _ctx_args(*, since, live, orders_char, record, refresh_minutes) -> dict:
    return {"now": _now(), "client": esi_mod.Esi(esi_mod.default_user_agent(sso.load_config())),
            "since": since, "live": live, "orders_char": orders_char, "record": record,
            "refresh_minutes": refresh_minutes}


def build_document(*, since: str | None, live: bool = True, refresh_minutes: int | None = None,
                   orders_char: str | None = None, record: bool = False, registry=None) -> dict:
    """Every panel on the configured pages, read once into one document. With `record` the live stock
    is also stored in the ledger as a snapshot, which is what builds the stock history."""
    from . import registry as registry_mod
    registry = registry or registry_mod.current()
    pages, notes = _layout(registry)
    args = _ctx_args(since=since, live=live, orders_char=orders_char, record=record, refresh_minutes=refresh_minutes)
    builds = panels.build(args, registry, pages, {}, log=_log)
    return panels.document(registry, pages, notes, builds, generated=_iso(args["now"]),
                           refresh_minutes=refresh_minutes)


def _page() -> bytes:
    return resources.files("newedenworks").joinpath("data/dashboard.html").read_bytes()


def _cache_path() -> str:
    return os.path.join(paths.cache_dir(), "dashboard.json")


class _State:
    """The served document and the refresh thread's bookkeeping, shared with the request handlers."""

    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.body: bytes | None = None
        self.refreshing = False
        self.last_attempt: float | None = None   # monotonic; None until the first refresh starts
        self.last_error: str | None = None
        self.next_refresh: str | None = None
        self.builds: dict[str, panels.Build] = {}       # each panel's latest part of the document
        self.pages: list[dict] = []
        self.notes: list[str] = []
        self.generated: str | None = None
        try:
            with open(_cache_path(), "rb") as fh:
                self.body = fh.read()       # the last document, so a restart serves at once
        except OSError:
            pass

    def status(self) -> dict:
        with self.lock:
            return {"refreshing": self.refreshing, "last_error": self.last_error,
                    "next_refresh": self.next_refresh, "has_document": self.body is not None}

    def _publish(self, doc: dict) -> None:
        body = json.dumps(doc).encode("utf-8")
        with contextlib.suppress(OSError):
            storage.atomic_write(_cache_path(), body)
        with self.lock:
            self.body = body

    def _build(self, *, quick: bool) -> dict:
        """Read the panels (only the quick ones when `quick`) and return the document of every panel's
        latest part; a panel that failed keeps its previous part."""
        from . import registry as registry_mod
        args = self.args
        registry = registry_mod.current()
        self.pages, self.notes = _layout(registry)
        ctx_args = _ctx_args(since=args.since, live=not args.no_live, orders_char=args.orders_char,
                             record=not quick, refresh_minutes=args.refresh)
        panels.build(ctx_args, registry, self.pages, self.builds, quick=quick, log=_log)
        return panels.document(registry, self.pages, self.notes, self.builds,
                               generated=self.generated if quick and self.generated else _iso(ctx_args["now"]),
                               refresh_minutes=args.refresh)

    def refresh(self) -> None:
        from . import registry as registry_mod
        args = self.args
        with self.lock:
            self.refreshing = True
            self.last_attempt = time.monotonic()
        started = time.monotonic()
        error = None
        try:
            sync = registry_mod.current().hook("ledger.sync_if_due")
            if not args.no_sync and sync is not None:
                line, problems = sync(esi_mod.Esi(esi_mod.default_user_agent(sso.load_config())),
                                      interval=max(60, args.refresh * 60 - 300))
                for message in ([line] if line else []) + problems:
                    _log(message)
            doc = self._build(quick=False)
            self.generated = doc["generated"]
            self._publish(doc)
            for part, key in (("jobs", "jobs"), ("orders", "orders"), ("stock", "stockpile")):
                if (doc.get(key) or {}).get("error"):
                    _log(f"{part}: {doc[key]['error']}")
            if ((doc.get("planetary") or {}).get("colonies") or {}).get("error"):
                _log(f"colonies: {doc['planetary']['colonies']['error']}")
            failed = [f"{pid}: {info['error']}" for pid, info in doc["layout"]["panels"].items() if info["error"]]
            error = "; ".join(failed) or None
            _log(f"document rebuilt in {time.monotonic() - started:.0f} s")
        except (RuntimeError, OSError, ValueError, esi_mod.EsiError) as err:
            error = str(err)
            _log(f"refresh failed: {err}")
        with self.lock:
            self.refreshing = False
            self.last_error = error
            self.next_refresh = _iso(_now() + timedelta(minutes=args.refresh))

    def refresh_orders(self) -> None:
        """Re-read only the quick panels (trade's orders) and swap their parts into the served document.

        A failed read keeps the previous rows, whose `esi_as_of` then says how old they are. It does
        not mark the server as refreshing: a manual refresh asked for meanwhile still runs right after."""
        with self.lock:
            if self.body is None or not self.builds:
                return
        started = time.monotonic()
        try:
            doc = self._build(quick=True)
        except (RuntimeError, OSError, ValueError, esi_mod.EsiError) as err:
            _log(f"orders refresh failed: {err}")
            return
        self._publish(doc)
        _log(f"orders refreshed in {time.monotonic() - started:.0f} s")

    def loop(self) -> None:
        """Full rebuilds every --refresh minutes (or on request), orders alone in between."""
        full_every = self.args.refresh * 60
        orders_every = (getattr(self.args, "orders_refresh", 0) or 0) * 60
        if orders_every >= full_every:
            orders_every = 0
        while True:
            self.refresh()
            next_full = time.monotonic() + full_every
            while True:
                now = time.monotonic()
                due = min(next_full, now + orders_every) if orders_every else next_full
                if self.wake.wait(max(0.0, due - now)):
                    self.wake.clear()
                    break           # a manual refresh: rebuild everything now
                if time.monotonic() >= next_full - 1:
                    break
                self.refresh_orders()

    def request_refresh(self) -> bool:
        with self.lock:
            recent = (self.last_attempt is not None
                      and time.monotonic() - self.last_attempt < MIN_MANUAL_REFRESH_SECONDS)
            if self.refreshing or recent:
                return False
        self.wake.set()
        return True


def _handler(state: _State, page: bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "new-eden-works-dashboard"

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def do_GET(self):   # noqa: N802 - the http.server API
            route = self.path.split("?", 1)[0]
            if route in ("/", "/index.html"):
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif route == "/api/dashboard":
                with state.lock:
                    body = state.body
                if body is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"building": True, **state.status()})
                else:
                    self._send(HTTPStatus.OK, body, "application/json")
            elif route == "/api/status":
                self._json(HTTPStatus.OK, state.status())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        do_HEAD = do_GET    # noqa: N815

        def do_POST(self):  # noqa: N802
            if self.path.split("?", 1)[0] != "/api/refresh":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            started = state.request_refresh()
            self._json(HTTPStatus.ACCEPTED if started else HTTPStatus.TOO_MANY_REQUESTS,
                       {"started": started, **state.status()})

        def log_message(self, fmt, *args):
            if state.args.verbose:
                _log(f"{self.address_string()} {fmt % args}")

    return Handler


def make_server(args) -> tuple[ThreadingHTTPServer, _State]:
    state = _State(args)
    server = ThreadingHTTPServer((args.host, args.port), _handler(state, _page()))
    server.daemon_threads = True
    return server, state


def cmd_serve(args) -> int:
    if args.refresh < 1:
        raise RuntimeError("--refresh must be at least 1 minute")
    if (getattr(args, "orders_refresh", 0) or 0) < 0:
        raise RuntimeError("--orders-refresh cannot be negative (0 turns it off)")
    from . import registry as registry_mod
    if registry_mod.current().plugin("ledger") is not None:
        from .ledger import cmd_ledger
        cmd_ledger._open().close()  # fail now, not in the refresh thread, when there is no ledger
    server, state = make_server(args)
    threading.Thread(target=state.loop, name="dashboard-refresh", daemon=True).start()
    host, port = server.server_address[:2]
    shown = "localhost" if host in ("127.0.0.1", "::1") else host
    orders_every = getattr(args, "orders_refresh", 0) or 0
    _log(f"serving http://{shown}:{port}/ (refresh every {args.refresh} min"
         f"{f', orders every {orders_every} min' if 0 < orders_every < args.refresh else ''}"
         f"{', no ledger sync' if args.no_sync else ''}{', no live reports' if args.no_live else ''})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_build(args) -> int:
    doc = build_document(since=args.since, live=not args.no_live, orders_char=args.orders_char)
    text = json.dumps(doc, indent=2)
    if args.out:
        storage.atomic_write(args.out, text)
    else:
        print(text)
    return 0


def cmd_dashboard(args) -> int:
    handler = {"serve": cmd_serve, "build": cmd_build}.get(args.dashboard_action)
    if handler is None:
        print("usage: neworks dashboard {serve,build} [--help]", file=sys.stderr)
        return 2
    return handler(args)


def _dashboard(p_dash):
    dash_sub = p_dash.add_subparsers(dest="dashboard_action")
    p_dserve = dash_sub.add_parser("serve", help="run the dashboard web server")
    p_dserve.add_argument("--host", default="0.0.0.0",
                          help="address to listen on (default 0.0.0.0: every interface, so the local network "
                               "can reach it; 127.0.0.1 for this machine only)")
    p_dserve.add_argument("--port", type=int, default=DEFAULT_PORT,
                          help=f"TCP port (default {DEFAULT_PORT})")
    p_dserve.add_argument("--refresh", type=int, default=DEFAULT_REFRESH_MINUTES, metavar="MIN",
                          help=f"rebuild the data every MIN minutes (default {DEFAULT_REFRESH_MINUTES})")
    p_dserve.add_argument("--orders-refresh", type=int, default=DEFAULT_ORDERS_REFRESH_MINUTES,
                          metavar="MIN",
                          help="between full rebuilds, re-read only the market orders every MIN minutes "
                               f"(default {DEFAULT_ORDERS_REFRESH_MINUTES}: ESI caches order books "
                               "that long; 0 = only with the full rebuild)")
    p_dserve.add_argument("--no-sync", action="store_true",
                          help="never run a ledger sync; report what is stored (another timer syncs)")
    p_dserve.add_argument("--verbose", action="store_true", help="log every HTTP request")
    p_dbuild = dash_sub.add_parser("build", help="write the dashboard document as JSON once and exit")
    p_dbuild.add_argument("--out", metavar="PATH", help="file to write (default: stdout)")
    for p_dash_action in (p_dserve, p_dbuild):
        p_dash_action.add_argument("--since", metavar="YYYY-MM-DD",
                                   help="first day of the 'since start' figures and the charts (default: the cutover)")
        p_dash_action.add_argument("--orders-char", metavar="CHAR",
                                   help="only this stored character's orders (default: every stored character)")
        p_dash_action.add_argument("--no-live", action="store_true",
                                   help="ledger figures only: skip the live industry status and order checks")


def _validate(settings: dict) -> list[str]:
    problems = panels.validate(settings)
    try:
        serve_argv(settings)
    except RuntimeError as err:
        problems.append(str(err))
    return problems


def plugin() -> Plugin:
    return Plugin(
        name="dashboard",
        version=__version__,
        api=API_VERSION,
        summary="a local web page of every loaded plugin's panels (profit, planetary industry, stock, slots)",
        config=ConfigSpec(defaults={}, validate=_validate),
        commands=(Command(("dashboard",), help=("web page with profit over time, by product and by market, plus "
                                                "industry slots and market orders (refreshes itself hourly)"),
                          configure=_dashboard, handler=cmd_dashboard),),
        # Off until config.json's service.jobs.dashboard turns it on, so the service can run next to
        # the eve-dashboard unit that holds the port today.
        jobs=(Job("dashboard", every=DAEMON, run=serve_job, default=False,
                  help="dashboard serve with config.json's dashboard section"),),
    )
