"""Planetary's MCP tool: the live colonies with their extractor timers."""

from __future__ import annotations

from ..mcp import flags, schema, string
from ..plugin import Tool

TOOLS = (
    Tool("colonies", command=("colonies",), title="Colonies",
         description="Every colony with its extractors, what they make and when each extraction ends. ESI only "
                     "refreshes a colony when it is opened in game: say how old each one is.",
         input_schema=schema(char=string("stored character name or id")), args=flags("--detail", char="--char")),
)
