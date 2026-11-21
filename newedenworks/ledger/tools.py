"""The ledger's MCP tools: progress, notes, profit, stock and read-only SQL."""

from __future__ import annotations

from ..mcp import boolean, flags, integer, integers, schema, string
from ..plugin import Tool

SINCE = string("first day, YYYY-MM-DD")
UNTIL = string("last day, YYYY-MM-DD")
KIND = string("note kind", ("update", "decision", "todo", "incident", "analysis"))

TOOLS = (
    Tool("ledger_progress", command=("ledger", "progress"), title="Ledger progress",
         description="Per-day figures, open to-dos and recent notes: where the operation stands.",
         input_schema=schema(since=SINCE, notes=integer("recent notes to include (default 5)", 1)),
         args=flags(since="--since", notes="--notes")),
    Tool("ledger_notes", command=("ledger", "note"), title="Ledger notes",
         description="Notes by id, or the newest ones (the newest note is where to pick up).",
         input_schema=schema(ids=integers("note ids"), last=integer("the newest N", 1), kind=KIND),
         args=flags("show", last="--last", kind="--kind", ids=None)),
    Tool("ledger_note_search", command=("ledger", "note"), title="Search notes",
         description="List notes by kind, date, open to-dos or text.",
         input_schema=schema(kind=KIND, since=SINCE, open=boolean("open to-dos only"), grep=string("text to find"),
                             limit=integer("at most N", 1)),
         args=flags("list", kind="--kind", since="--since", open="--open", grep="--grep", limit="--limit")),
    Tool("ledger_pnl", command=("ledger", "pnl"), title="Profit and loss",
         description="Profit and loss with its assumptions block; report the assumptions with the figures.",
         input_schema=schema(since=SINCE, until=UNTIL, by=string("period", ("total", "day", "week", "month"))),
         args=flags(since="--since", until="--until", by="--by")),
    Tool("ledger_products", command=("ledger", "products"), title="Profit per product",
         description="Profit, margin and unit cost per product.",
         input_schema=schema(since=SINCE, until=UNTIL, limit=integer("at most N", 1),
                             scope=string("line kind", ("all", "invention", "other", "disposal", "planetary"))),
         args=flags(since="--since", until="--until", scope="--scope", limit="--limit")),
    Tool("ledger_invention", command=("ledger", "invention"), title="Invention lines",
         description="Invention lines: attempts, successes, cost per success and decryptors.",
         input_schema=schema(), args=flags()),
    Tool("ledger_inventory", command=("ledger", "inventory"), title="Stock at cost",
         description="Stock held at ledger cost and at market, per item.",
         input_schema=schema(all=boolean("every item, not only the top ones"), limit=integer("at most N", 1)),
         args=flags(all="--all", limit="--limit")),
    Tool("ledger_planetary", command=("ledger", "planetary"), title="Planetary industry",
         description="Planetary industry as its own operation: output, sales, customs, construction.",
         input_schema=schema(since=SINCE, until=UNTIL, by=string("period", ("total", "day", "week", "month"))),
         args=flags(since="--since", until="--until", by="--by")),
    Tool("ledger_sql", command=("ledger", "sql"), title="Ledger SQL",
         description="One read-only SELECT on the ledger (type_name(), character_name(), local_time() "
                     "available); see the newedenworks://store/schema resource for the tables.",
         input_schema=schema(required=("query",), query=string("a SELECT statement"), limit=integer("row limit", 1)),
         args=flags(limit="--limit", query=None)),
    Tool("ledger_sync", command=("ledger", "sync"), title="Sync the ledger", read_only=False,
         description="Pull wallet, jobs and orders from ESI into the ledger.",
         input_schema=schema(), args=flags()),
    Tool("ledger_note_add", command=("ledger", "note"), title="Add a note", read_only=False, idempotent=False,
         description="Record progress or a decision as a ledger note.",
         input_schema=schema(required=("title",), title=string("note title"), body=string("note text"), kind=KIND,
                             at=string("ISO time to backdate to")),
         args=flags("add", kind="--kind", body="--body", at="--at", title=None)),
)
