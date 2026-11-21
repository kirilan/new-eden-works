"""`neworks mcp`: the MCP server - every loaded plugin's tools and resources for an agent.

An agent (Claude Code, Codex, Hermes, Claude Desktop) starts `new-eden-works mcp` and speaks JSON-RPC 2.0
over stdin and stdout, one message per line (the MCP stdio transport); nothing else is ever written
to stdout, and logs go to stderr. Standard library only.

- **Tools** are the plugins' `Tool`s. A call checks the arguments against the tool's input schema,
  builds the command line (`tool.args`) and runs it as a child process with `--json`, exactly as a
  person would type it: the CLI and MCP give the same answer, a failing or hanging command cannot take
  the server down, and nothing a command prints can corrupt the protocol stream.
- **Read-only unless allowed.** A tool that changes state (`read_only=False`: a ledger note, a saved
  round, a ledger sync, a Telegram test) is neither listed nor callable until config.json names it in
  `mcp.allow_writes`.
- **Resources** are reference text read on demand: this installation's playbooks and operation file
  (from the config folder, never from a package), the ledger's schema, and the agent reference when
  the server runs from a source checkout.
- **Prompts** are the plugins' agent skills (`newedenworks.agent`): the skill's instructions, with
  this installation's playbook for it attached when there is one; each skill's SKILL.md is also a
  resource (`newedenworks://skills/<name>`).

`neworks mcp --list` prints the tools and resources this installation offers, for a person.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TextIO

from . import __version__, paths
from .plugin import Command, Tool

# Newest first; the client's version is echoed when it is one of these, else the newest is offered.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "new-eden-works"
TOOL_TIMEOUT_SECONDS = 600
# A tool's answer larger than this is refused with a hint to narrow the call: an agent cannot use a
# megabyte of JSON in its context, and some agents cannot take it at all.
MAX_RESULT_CHARS = 1_000_000
INSTRUCTIONS = (
    "New Eden Works: an EVE Online operation's characters, industry, ledger and market. Start from "
    "the operation (resource newedenworks://operation, tool operation_show) and the playbook of the "
    "task (newedenworks://playbook/<skill>): its rules win over a skill's defaults. Data comes from "
    "ESI caches; keep timestamps, staleness warnings and assumptions in the answer, and never replace "
    "missing data with zeros. Tools that change state appear only when config.json's mcp.allow_writes "
    "lists them.")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


# -- declaring tools -------------------------------------------------------------------------------

def string(description: str, enum: Iterable[str] | None = None) -> dict:
    out = {"type": "string", "description": description}
    if enum is not None:
        out["enum"] = list(enum)
    return out


def boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


def integer(description: str, minimum: int | None = None) -> dict:
    out = {"type": "integer", "description": description}
    if minimum is not None:
        out["minimum"] = minimum
    return out


def number(description: str) -> dict:
    return {"type": "number", "description": description}


def strings(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def integers(description: str) -> dict:
    return {"type": "array", "items": {"type": "integer"}, "description": description}


def schema(required: Iterable[str] = (), **properties: dict) -> dict:
    """An input schema: an object of these properties, nothing else allowed."""
    out = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        out["required"] = list(required)
    return out


def flags(*fixed: str, **mapping: str | None) -> Callable[[dict], list[str]]:
    """`tool.args` for the common case: the fixed words first, then each given argument as its flag.

    `mapping` is argument name -> flag (`"--site"`), or None for a positional argument. A true boolean
    is the bare flag, a list repeats the flag for each item (or adds each item when positional), and
    anything else is the flag followed by its value."""
    def build(arguments: dict) -> list[str]:
        out = list(fixed)
        positional: list[str] = []
        for name, flag in mapping.items():
            value = arguments.get(name)
            if value is None or value is False:
                continue
            items = value if isinstance(value, list) else [value]
            for item in items:
                if flag is None:
                    positional.append(str(item))
                elif item is True:
                    out.append(flag)
                else:
                    out += [flag, str(item)]
        return out + positional
    return build


# -- the registry's tools --------------------------------------------------------------------------

def all_tools(registry) -> list[tuple[Tool, str]]:
    """Every loaded plugin's tools with the plugin offering each, in load order."""
    out = []
    for loaded in registry.plugins:
        for tool in loaded.plugin.tools:
            out.append((tool, loaded.plugin.name))
    return out


def allowed_writes(cfg: dict | None = None) -> set[str]:
    from . import sso
    cfg = sso.load_config() if cfg is None else cfg
    section = cfg.get("mcp") if isinstance(cfg, dict) else None
    allowed = section.get("allow_writes") if isinstance(section, dict) else None
    return {name for name in allowed if isinstance(name, str)} if isinstance(allowed, list) else set()


def offered(registry, cfg: dict | None = None) -> list[tuple[Tool, str]]:
    """The tools this server lists and accepts: every read-only one, and the writes allowed."""
    allowed = allowed_writes(cfg)
    return [(tool, plugin) for tool, plugin in all_tools(registry) if tool.read_only or tool.name in allowed]


def describe(tool: Tool) -> dict:
    return {
        "name": tool.name,
        "title": tool.title or tool.name.replace("_", " "),
        "description": tool.description or "",
        "inputSchema": dict(tool.input_schema or schema()),
        "annotations": {"title": tool.title or tool.name.replace("_", " "), "readOnlyHint": tool.read_only,
                        "destructiveHint": tool.destructive, "idempotentHint": tool.idempotent,
                        "openWorldHint": True},
    }


def check_arguments(tool: Tool, arguments: Any) -> str | None:
    """What is wrong with `arguments` for this tool's schema (the subset tools use), or None."""
    spec = tool.input_schema or schema()
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    props = spec.get("properties") or {}
    for name in spec.get("required") or []:
        if arguments.get(name) in (None, "", []):
            return f"missing required argument {name}"
    kinds = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list}
    for name, value in arguments.items():
        prop = props.get(name)
        if prop is None:
            return f"unknown argument {name} (known: {', '.join(props) or 'none'})"
        expected = kinds.get(prop.get("type"))
        if value is None or expected is None:
            continue
        if not isinstance(value, expected) or (prop.get("type") in ("integer", "number") and isinstance(value, bool)):
            return f"argument {name} must be of type {prop['type']}"
        if prop.get("type") == "array":
            item_kind = (prop.get("items") or {}).get("type", "string")
            wanted = int if item_kind == "integer" else str
            if not all(isinstance(item, wanted) and not isinstance(item, bool) for item in value):
                return f"argument {name} must be a list of {item_kind}s"
        if "enum" in prop and value not in prop["enum"]:
            return f"argument {name} must be one of {', '.join(prop['enum'])}"
        if "minimum" in prop and value < prop["minimum"]:
            return f"argument {name} must be at least {prop['minimum']}"
    return None


def self_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "newedenworks"]


def command_line(tool: Tool, arguments: dict) -> list[str]:
    argv = [*tool.command, *(tool.args(arguments) if tool.args else [])]
    if tool.json and "--json" not in argv:
        argv.append("--json")
    return argv


def run_tool(tool: Tool, arguments: dict, runner=subprocess.run) -> dict:
    """A tools/call result: the command's JSON as structured content (and as text), or its error."""
    argv = command_line(tool, arguments)
    try:
        done = runner([*self_command(), *argv], capture_output=True, text=True, encoding="utf-8",
                      timeout=TOOL_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return _error_result(f"neworks {' '.join(argv)} timed out after {TOOL_TIMEOUT_SECONDS} s")
    out, err = done.stdout or "", (done.stderr or "").strip()
    if done.returncode:
        tail = "\n".join(err.splitlines()[-20:]) or f"exit {done.returncode}"
        return _error_result(f"neworks {' '.join(argv)} failed:\n{tail}" + (f"\n\n{out.strip()}" if out.strip() else ""))
    data = None
    if tool.json:
        try:
            data = json.loads(out)
        except ValueError:
            data = None
    # The JSON goes out compact: the commands indent it for people, which costs an agent a third more.
    text = out if data is None else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        return _error_result(f"neworks {' '.join(argv)} answered {len(text) // 1000} kB, more than an agent can "
                             f"use; narrow the call (a site, a character, since, limit) and try again")
    content = [{"type": "text", "text": text}]
    if err:
        # Warnings, staleness notes and hints a person would have seen under the output.
        content.append({"type": "text", "text": "stderr:\n" + err})
    result: dict = {"content": content, "isError": False}
    if data is not None:
        result["structuredContent"] = data if isinstance(data, dict) else {"result": data}
    return result


def _error_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


# -- resources -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Resource:
    uri: str
    name: str
    description: str
    mime_type: str
    read: Callable[[], str]


def _read_file(path: str) -> Callable[[], str]:
    def read() -> str:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return read


def _cli_text(*argv: str) -> Callable[[], str]:
    def read() -> str:
        done = subprocess.run([*self_command(), *argv], capture_output=True, text=True, encoding="utf-8",
                              timeout=TOOL_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
        if done.returncode:
            tail = (done.stderr or "").strip().splitlines()[-1:] or [f"exit {done.returncode}"]
            raise RuntimeError(f"neworks {' '.join(argv)}: {tail[0]}")
        return done.stdout
    return read


def _repo_doc(name: str) -> str | None:
    """A file under docs/ when running from a source checkout (an installed package has none)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "docs", name)
    return path if os.path.isfile(path) else None


def resources(registry) -> list[Resource]:
    out: list[Resource] = []
    op_path = os.path.join(paths.config_dir(create=False), "operation.json")
    if os.path.isfile(op_path):
        out.append(Resource("newedenworks://operation", "operation",
                            "The operation file: sites, facilities, hubs, freight, planning, selling",
                            "application/json", _read_file(op_path)))
    book = paths.playbook_dir()
    if os.path.isdir(book):
        for name in sorted(os.listdir(book)):
            if name.endswith(".md"):
                stem = name[:-3]
                out.append(Resource(f"newedenworks://playbook/{stem}", f"playbook {stem}",
                                    f"This installation's own rules for {stem}; they win over a skill's defaults",
                                    "text/markdown", _read_file(os.path.join(book, name))))
    if registry.plugin("ledger") is not None:
        out.append(Resource("newedenworks://store/schema", "ledger schema",
                            "The ledger's tables and columns (what ledger_sql can query)",
                            "text/plain", _cli_text("ledger", "schema")))
    from . import agent
    for skill in agent.skills(registry):
        out.append(Resource(f"newedenworks://skills/{skill.name}", f"skill {skill.name}",
                            skill.description, "text/markdown", _read_file(skill.skill_file)))
    reference = _repo_doc("agent-reference.md")
    if reference:
        out.append(Resource("newedenworks://docs/agent-reference", "agent reference",
                            "JSON shapes of the commands and the ledger tables, with their traps",
                            "text/markdown", _read_file(reference)))
    return out


# -- the server ------------------------------------------------------------------------------------

class Server:
    def __init__(self, registry, cfg: dict | None = None, runner=subprocess.run) -> None:
        self.registry = registry
        self.cfg = cfg
        self.runner = runner

    def tools(self) -> dict[str, Tool]:
        return {tool.name: tool for tool, _plugin in offered(self.registry, self.cfg)}

    def handle(self, message: Any) -> dict | None:
        """The response to one JSON-RPC message (None for a notification)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _rpc_error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST,
                              "not a JSON-RPC 2.0 request")
        method, params, ident = message["method"], message.get("params") or {}, message.get("id")
        if "id" not in message:
            return None                     # notifications (initialized, cancelled) need no answer
        try:
            result = self.dispatch(method, params)
        except _RpcError as err:
            return _rpc_error(ident, err.code, err.message)
        except Exception as err:           # noqa: BLE001 - one request's failure is that request's error
            return _rpc_error(ident, -32603, f"{type(err).__name__}: {err}")
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                                     "prompts": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": "New Eden Works", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [describe(tool) for tool in self.tools().values()]}
        if method == "tools/call":
            tool = self.tools().get(params.get("name"))
            if tool is None:
                raise _RpcError(INVALID_PARAMS, f"unknown tool {params.get('name')!r}")
            arguments = params.get("arguments") or {}
            problem = check_arguments(tool, arguments)
            if problem:
                return _error_result(f"{tool.name}: {problem}")
            return run_tool(tool, arguments, self.runner)
        if method == "resources/list":
            return {"resources": [{"uri": r.uri, "name": r.name, "description": r.description,
                                   "mimeType": r.mime_type} for r in resources(self.registry)]}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            found = {r.uri: r for r in resources(self.registry)}.get(params.get("uri"))
            if found is None:
                raise _RpcError(-32002, f"resource not found: {params.get('uri')}")
            return {"contents": [{"uri": found.uri, "mimeType": found.mime_type, "text": found.read()}]}
        if method == "prompts/list":
            from . import agent
            return {"prompts": [{"name": skill.name, "title": skill.name.replace("-", " ").capitalize(),
                                 "description": skill.description, "arguments": [PROMPT_SITE]}
                                for skill in agent.skills(self.registry)]}
        if method == "prompts/get":
            return prompt(self.registry, params.get("name"), params.get("arguments") or {})
        if method == "logging/setLevel":
            return {}
        raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def serve(self, stdin: TextIO, stdout: TextIO) -> int:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError as err:
                response = _rpc_error(None, PARSE_ERROR, f"parse error: {err}")
            else:
                response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
        return 0


PROMPT_SITE = {"name": "site", "description": "site of the operation file (default: its default site)",
               "required": False}


def prompt(registry, name, arguments: dict) -> dict:
    """A skill as a prompt: its instructions to follow with this server's tools, and the installation's
    playbook for it (whose rules win over the skill's defaults) when there is one."""
    from . import agent
    skill = next((s for s in agent.skills(registry) if s.name == name), None)
    if skill is None:
        raise _RpcError(INVALID_PARAMS, f"unknown prompt {name!r}")
    site = arguments.get("site") if isinstance(arguments, dict) else None
    intro = (f"Follow the {skill.name} skill below, using the neworks MCP tools (a command `neworks X Y` "
             f"in it is the tool `X_Y`)." + (f" Work on site {site}." if site else ""))
    messages = [{"role": "user", "content": {"type": "text", "text": f"{intro}\n\n{skill.body()}"}}]
    book = os.path.join(paths.playbook_dir(), f"{skill.name}.md")
    if os.path.isfile(book):
        with open(book, encoding="utf-8") as fh:
            messages.append({"role": "user", "content": {"type": "resource", "resource": {
                "uri": f"newedenworks://playbook/{skill.name}", "mimeType": "text/markdown", "text": fh.read()}}})
    return {"description": skill.description, "messages": messages}


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _rpc_error(ident, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


# -- the command -----------------------------------------------------------------------------------

def _mcp_args(p) -> None:
    p.add_argument("--list", action="store_true",
                   help="print the tools and resources this installation offers, instead of serving")


def list_text(registry) -> str:
    from . import render
    allowed = allowed_writes()
    rows = []
    for tool, plugin in all_tools(registry):
        state = "read" if tool.read_only else ("write, allowed" if tool.name in allowed else "write, hidden")
        line = [*tool.command, *(tool.args({}) if tool.args else [])]
        rows.append([tool.name, plugin, state, "neworks " + " ".join(line)])
    lines = [render.table(["tool", "plugin", "kind", "runs"], rows)]
    lines.append("resources: " + ", ".join(r.uri for r in resources(registry)))
    lines.append("a write tool is offered once config.json lists it in mcp.allow_writes")
    return "\n\n".join(lines)


def cmd_mcp(args) -> int:
    from . import registry as registry_mod
    registry = registry_mod.current()
    if args.list:
        print(list_text(registry))
        return 0
    stdin = open(sys.stdin.fileno(), encoding="utf-8", closefd=False)
    return Server(registry).serve(stdin, sys.stdout)


COMMAND = Command(("mcp",), help="the MCP server for agents (stdio): every plugin's tools, plus playbooks and "
                                 "the operation file as resources",
                  configure=_mcp_args, handler=cmd_mcp)
