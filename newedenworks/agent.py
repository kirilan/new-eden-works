"""The plugins' agent skills, and `neworks agent install`, which puts them where an agent reads them.

A plugin ships its skills as folders under `Plugin.skills`, each with a `SKILL.md` in the common
format (YAML front matter with `name` and `description`, then the instructions) and a
`playbook.example.md`. The MCP server offers each as a prompt and as a resource; `agent install`
copies them for an agent that reads skill folders, and prints the MCP server entry to add to that
agent's configuration:

- `claude-code`: `.claude/skills` of the current project (`--user`: `~/.claude/skills`), and the
  `.mcp.json` entry / `claude mcp add` line;
- `codex`: `$CODEX_HOME/skills` (default `~/.codex/skills`), and the `config.toml` table;
- `hermes`: `$HERMES_HOME/skills` (default `~/.hermes/skills`), and the `config.yaml` entry.

An installed folder carries a `.neworks-skill` marker. A reinstall replaces marked folders and leaves
any other folder of the same name alone unless `--force`: a skill the user wrote is never overwritten.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass

from .plugin import Command

MARKER = ".neworks-skill"
TARGETS = ("claude-code", "codex", "hermes")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    plugin: str
    path: str                      # the skill's folder

    @property
    def skill_file(self) -> str:
        return os.path.join(self.path, "SKILL.md")

    def text(self) -> str:
        with open(self.skill_file, encoding="utf-8") as fh:
            return fh.read()

    def body(self) -> str:
        """The instructions without the front matter."""
        text = self.text()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                return text[end + 4:].lstrip("\n")
        return text


def front_matter(text: str) -> dict[str, str]:
    """`name` and `description` (and any other `key: value` line) of a SKILL.md's front matter."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    out: dict[str, str] = {}
    for line in text[3:end if end != -1 else len(text)].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith(" "):
            out[key.strip()] = value.strip().strip('"')
    return out


def skills(registry) -> list[Skill]:
    """Every loaded plugin's skills, in load order; a folder without a SKILL.md is not a skill."""
    out: list[Skill] = []
    for loaded in registry.plugins:
        root = loaded.plugin.skills
        if not root or not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            folder = os.path.join(root, name)
            path = os.path.join(folder, "SKILL.md")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                meta = front_matter(fh.read())
            out.append(Skill(meta.get("name") or name, meta.get("description", ""), loaded.plugin.name, folder))
    return out


# -- install ---------------------------------------------------------------------------------------

def destination(target: str, user: bool = False, cwd: str | None = None) -> str:
    home = os.path.expanduser("~")
    if target == "claude-code":
        return os.path.join(home, ".claude", "skills") if user else os.path.join(cwd or os.getcwd(), ".claude", "skills")
    if target == "codex":
        return os.path.join(os.environ.get("CODEX_HOME") or os.path.join(home, ".codex"), "skills")
    if target == "hermes":
        return os.path.join(os.environ.get("HERMES_HOME") or os.path.join(home, ".hermes"), "skills")
    raise RuntimeError(f"unknown target {target} (known: {', '.join(TARGETS)})")


def install(found: list[Skill], dest: str, force: bool = False) -> tuple[list[str], list[str]]:
    """Copy each skill folder into `dest`: (installed names, skipped names with why)."""
    os.makedirs(dest, exist_ok=True)
    done, skipped = [], []
    for skill in found:
        target = os.path.join(dest, skill.name)
        if os.path.exists(target) and not os.path.exists(os.path.join(target, MARKER)) and not force:
            skipped.append(f"{skill.name}: {target} exists and was not installed by neworks (--force replaces it)")
            continue
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(skill.path, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with open(os.path.join(target, MARKER), "w", encoding="utf-8") as fh:
            json.dump({"plugin": skill.plugin, "source": skill.path}, fh)
            fh.write("\n")
        done.append(skill.name)
    return done, skipped


def server_command() -> list[str]:
    """How an agent starts this installation's MCP server."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "mcp"]
    return [sys.executable, "-m", "newedenworks", "mcp"]


def config_text(target: str) -> str:
    command, *args = server_command()
    if target == "claude-code":
        entry = {"mcpServers": {"neworks": {"command": command, "args": args}}}
        return ("Add the MCP server to the project's .mcp.json:\n\n" + json.dumps(entry, indent=2)
                + "\n\nor run:\n\n  claude mcp add --scope project neworks -- " + " ".join([command, *args]))
    if target == "codex":
        return ("Add the MCP server to ~/.codex/config.toml:\n\n[mcp_servers.neworks]\n"
                f"command = {json.dumps(command)}\nargs = {json.dumps(args)}")
    return ("Add the MCP server to ~/.hermes/config.yaml:\n\nmcp_servers:\n  neworks:\n"
            f"    command: {json.dumps(command)}\n    args: {json.dumps(args)}")


def _agent_args(p) -> None:
    sub = p.add_subparsers(dest="agent_action", required=True, metavar="ACTION")
    p_install = sub.add_parser("install", help="copy the enabled plugins' skills to where the agent reads them "
                                               "and print the MCP server entry for it")
    p_install.add_argument("--target", choices=TARGETS, required=True, help="the agent")
    p_install.add_argument("--dest", metavar="DIR", help="skills folder to write (default: the agent's own)")
    p_install.add_argument("--user", action="store_true",
                           help="claude-code: the user's ~/.claude/skills instead of this project's")
    p_install.add_argument("--force", action="store_true",
                           help="also replace a folder of the same name that neworks did not install")
    sub.add_parser("skills", help="list the skills the loaded plugins ship")


def cmd_agent(args) -> int:
    from . import registry as registry_mod
    found = skills(registry_mod.current())
    if args.agent_action == "skills":
        for skill in found:
            print(f"{skill.name} ({skill.plugin}): {skill.description}")
        return 0
    dest = os.path.abspath(os.path.expanduser(args.dest)) if args.dest else destination(args.target, args.user)
    done, skipped = install(found, dest, args.force)
    print(f"installed {len(done)} skill(s) in {dest}: {', '.join(done) or 'none'}")
    for line in skipped:
        print(f"skipped {line}", file=sys.stderr)
    print()
    print(config_text(args.target))
    return 1 if skipped and not done else 0


COMMAND = Command(("agent",), help="the agent skills the plugins ship, and installing them for Claude Code, Codex "
                                   "or Hermes with the MCP server entry",
                  configure=_agent_args, handler=cmd_agent)
