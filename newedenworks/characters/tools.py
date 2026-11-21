"""The characters plugin's MCP tools: skills and the fleet summary."""

from __future__ import annotations

from ..mcp import boolean, flags, schema, string
from ..plugin import Tool

TOOLS = (
    Tool("characters_summary", command=("summary",), title="Characters",
         description="One line per stored character: clone state, skill points and training queue, plus totals.",
         input_schema=schema(), args=flags()),
    Tool("skills", command=("skills",), title="Skills",
         description="A character's skills, training queue and clone state (alpha/omega).",
         input_schema=schema(char=string("stored character name or id (default: every one)"),
                             trained_only=boolean("omit the training queue")),
         args=flags(char="--char", trained_only="--trained-only")),
)
