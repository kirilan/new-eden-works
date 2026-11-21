"""Dashboard panels: the context a panel reads with, and the document built from every active panel.

A plugin offers `Panel`s (`newedenworks.plugin`). The dashboard config (config.json's `dashboard`
section, `pages`) chooses which panels appear on which page and in what order; the default is every
loaded panel on one page, built-in panels in the order of today's page. Each panel's `data(ctx)`
returns its part of the document:

- kind `page`: a fragment merged into the document the page already draws (`ranges`, `jobs`,
  `orders`, ...); the page has its own code for each built-in panel;
- kind `kpi`, `table`, `list` or `timeseries`: the panel's data alone, which the page draws
  generically (see `GENERIC_KINDS`), so a plugin needs no front-end code.

Live reports run in child processes (`live_report`), started by every panel's `prefetch` before any panel
builds, so they run side by side while the ledger replays. A panel that fails keeps its previous part
of the document and says why (`panel_errors`); the others still refresh.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

# A live report that takes longer than this has hung on ESI; the panel keeps its last good part.
LIVE_TIMEOUT_SECONDS = 600
PAGE = "page"
GENERIC_KINDS = ("kpi", "table", "list", "timeseries")
# The built-in panels in the order of the page they were cut from; the default layout.
DEFAULT_ORDER = ("ledger.profit", "ledger.planetary", "planetary.colonies", "ledger.stockpile",
                 "industry.slots", "trade.orders")


class PanelSkip(Exception):
    """Raised by a panel's `data` to keep its previous part of the document (and log why)."""


def _self_command() -> list[str]:
    """How to start this tool again in a child process.

    From an installed package that is the interpreter plus `-m newedenworks`. In the frozen Windows
    build (PyInstaller) `sys.executable` is neworks.exe itself, which has no `-m`: it would read
    `-m` as an unknown flag and every live section would fail."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "newedenworks"]


def live_report(*args: str) -> tuple[dict | None, str | None]:
    """Run one of our own reports with --json in a child process: (document, error)."""
    command = [*_self_command(), *args, "--json"]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=LIVE_TIMEOUT_SECONDS,
                             encoding="utf-8")
    except subprocess.TimeoutExpired:
        return None, f"neworks {' '.join(args)} timed out after {LIVE_TIMEOUT_SECONDS} s"
    if run.returncode or not run.stdout.strip():
        tail = (run.stderr or "").strip().splitlines()[-1:] or [f"exit {run.returncode}"]
        return None, f"neworks {' '.join(args)}: {tail[0]}"
    try:
        return json.loads(run.stdout), None
    except json.JSONDecodeError as err:
        return None, f"neworks {' '.join(args)}: unreadable JSON ({err})"


@dataclass
class PanelContext:
    """What every panel of one build shares: the clock, ESI, the server's options and a memo.

    `start(*args)` begins a live report in the background (idempotent) and `report(*args)` waits for
    it; `once(key, fn)` computes a value one panel of the build needs and another may reuse (the
    ledger replay, the industry status). `quick` is true between full rebuilds, when only quick
    panels are read. `settings` is the panel's own entry in the dashboard config."""

    registry: Any
    now: datetime
    client: Any
    since: str | None = None
    live: bool = True
    orders_char: str | None = None
    record: bool = False
    refresh_minutes: int | None = None
    quick: bool = False
    settings: dict = field(default_factory=dict)
    runner: Callable[..., tuple[dict | None, str | None]] = live_report
    _memo: dict = field(default_factory=dict)
    _reports: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pool: ThreadPoolExecutor | None = None

    def hook(self, name: str):
        return self.registry.hook(name)

    def once(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def start(self, *args: str) -> None:
        with self._lock:
            if args in self._reports:
                return
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=8)
            self._reports[args] = self._pool.submit(self.runner, *args)

    def report(self, *args: str) -> tuple[dict | None, str | None]:
        self.start(*args)
        future: Future = self._reports[args]
        return future.result()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)


def merge(doc: dict, fragment: dict) -> dict:
    """`fragment` merged into `doc`: nested objects merge, everything else is replaced."""
    for key, value in fragment.items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            merge(doc[key], value)
        else:
            doc[key] = value
    return doc


def loaded(registry) -> dict:
    """Every loaded panel by id, with the plugin offering it."""
    return {panel.id: (panel, plugin) for panel, plugin in registry.panels()}


def layout(registry, section: dict | None) -> tuple[list[dict], list[str]]:
    """(pages, notes): the configured pages with each panel as {"id", "settings"}, and a note per panel
    named in the config that no loaded plugin offers (skipped)."""
    available = loaded(registry)
    pages = (section or {}).get("pages")
    notes: list[str] = []
    if not pages:
        ids = [p for p in DEFAULT_ORDER if p in available] + [p for p in available if p not in DEFAULT_ORDER]
        return [{"title": "Operation", "panels": [{"id": p, "settings": {}} for p in ids]}], notes
    out = []
    for page in pages:
        entries = []
        for entry in page.get("panels") or []:
            panel_id = entry if isinstance(entry, str) else entry.get("panel")
            settings = {} if isinstance(entry, str) else {k: v for k, v in entry.items() if k != "panel"}
            if panel_id in available:
                entries.append({"id": panel_id, "settings": settings})
            else:
                notes.append(f"panel {panel_id} is not offered by any loaded plugin; skipped")
        out.append({"title": str(page.get("title") or ""), "panels": entries})
    return out, notes


def validate(section: dict) -> list[str]:
    """What is wrong with the `pages` of the dashboard section (doctor)."""
    pages = section.get("pages")
    if pages is None:
        return []
    if not isinstance(pages, list) or not all(isinstance(p, dict) for p in pages):
        return ["dashboard.pages must be a list of {\"title\": ..., \"panels\": [...]}"]
    problems = []
    for i, page in enumerate(pages):
        panels = page.get("panels")
        if not isinstance(panels, list):
            problems.append(f"dashboard.pages[{i}].panels must be a list")
            continue
        for entry in panels:
            if not (isinstance(entry, str) or (isinstance(entry, dict) and isinstance(entry.get("panel"), str))):
                problems.append(f"dashboard.pages[{i}]: a panel is a name or {{\"panel\": name, ...settings}}")
    return problems


@dataclass
class Build:
    """One panel's latest part of the document and the error of its last read (None when it worked)."""

    fragment: dict | None = None
    error: str | None = None


def build(ctx_args: dict, registry, pages: list[dict], builds: dict[str, Build], *, quick: bool = False,
          log: Callable[[str], None] = lambda message: None) -> dict[str, Build]:
    """Read every panel on the pages (only the quick ones when `quick`) into `builds`, keeping a failed
    panel's previous part. Prefetches first, so live reports run side by side."""
    available = loaded(registry)
    wanted: dict[str, dict] = {}
    for page in pages:
        for entry in page["panels"]:
            wanted.setdefault(entry["id"], entry["settings"])
    chosen = [(pid, settings) for pid, settings in wanted.items() if not quick or available[pid][0].quick]
    ctx = PanelContext(registry=registry, quick=quick, **ctx_args)
    try:
        for pid, settings in chosen:
            panel = available[pid][0]
            if panel.prefetch is not None:
                ctx.settings = settings
                panel.prefetch(ctx)
        for pid, settings in chosen:
            panel = available[pid][0]
            ctx.settings = settings
            previous = builds.get(pid) or Build()
            try:
                builds[pid] = Build(panel.data(ctx), None)
            except PanelSkip as skip:
                log(f"{pid}: {skip}")
                builds[pid] = Build(previous.fragment, previous.error)
            except Exception as err:   # noqa: BLE001 - one panel's failure is that panel's error
                log(f"{pid} failed: {type(err).__name__}: {err}")
                builds[pid] = Build(previous.fragment, f"{type(err).__name__}: {err}")
    finally:
        ctx.close()
    return builds


def document(registry, pages: list[dict], notes: list[str], builds: dict[str, Build], *,
             generated: str, refresh_minutes: int | None) -> dict:
    """The served document: every page panel's fragment merged, the generic panels' data, the layout."""
    available = loaded(registry)
    doc: dict = {"generated": generated, "refresh_minutes": refresh_minutes}
    generic: dict[str, Any] = {}
    info: dict[str, dict] = {}
    order = [entry["id"] for page in pages for entry in page["panels"]]
    for pid in dict.fromkeys(order):
        panel, plugin = available[pid]
        result = builds.get(pid) or Build()
        info[pid] = {"title": panel.title, "kind": panel.kind, "plugin": plugin, "error": result.error}
        if result.fragment is None:
            continue
        if panel.kind == PAGE:
            merge(doc, json.loads(json.dumps(result.fragment)))
        else:
            generic[pid] = result.fragment
    doc["layout"] = {"pages": [{"title": p["title"], "panels": [e["id"] for e in p["panels"]]} for p in pages],
                     "panels": info, "notes": notes}
    doc["generic"] = generic
    return doc
