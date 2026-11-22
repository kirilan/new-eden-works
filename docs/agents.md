# Agents: MCP server and skills

New Eden Works does not run a language model of its own. It gives an agent you already use (Claude Code,
Codex, Hermes, Claude Desktop, ...) two things: an MCP server with every command as a tool, and agent
skills that turn recurring requests into tested workflows.

## Setting an agent up

```bash
neworks agent install --target claude-code         # into ./.claude/skills, plus the .mcp.json entry to add
neworks agent install --target claude-code --user  # into ~/.claude/skills instead
neworks agent install --target codex               # ~/.codex/skills and the config.toml table
neworks agent install --target hermes              # ~/.hermes/skills and the config.yaml entry
neworks agent skills                               # the skills the loaded plugins ship
```

`agent install` copies the skill folders (`SKILL.md` and `playbook.example.md`) and prints the MCP server
entry to add to that agent's configuration, with the command that starts this installation's server. For
Claude Code it is a `.mcp.json` in the project (or `claude mcp add`):

```json
{"mcpServers": {"neworks": {"command": "neworks", "args": ["mcp"]}}}
```

Run `agent install` again after an update: it replaces only the folders it installed (they carry a
`.neworks-skill` marker) and leaves a skill of yours with the same name alone unless `--force`.

## The MCP server

```bash
neworks mcp --list              # the tools and resources this installation offers
neworks mcp                     # serve MCP over stdin/stdout (what an agent starts)
```

`neworks mcp` is an MCP server (standard library only, JSON-RPC over stdio) that every plugin adds to.
Its **tools** are the plugins' commands behind small input schemas (`industry_status`, `industry_jobs`,
`job_board`, `build_cost`, `restock`, `production_supply`, `ledger_progress`, `ledger_pnl`,
`ledger_sql`, `colonies`, `skills`, `operation_show`, `doctor`, ...; `neworks mcp --list` shows them
all). A call runs the command as a child process with `--json` and returns its JSON, so the CLI and the
agent always get the same answer, and warnings the command printed come along. An answer over 1 MB is
refused with a hint to narrow the call. Its **resources** are this installation's operation file and
playbooks (read from the config folder), the ledger's schema, the skills, and
`docs/agent-reference.md` when run from a source checkout. Its **prompts** are the skills, each with
this installation's playbook attached, so an agent that only speaks MCP gets them too.

Tools that change state (`ledger_sync`, `ledger_note_add`, `production_round_save`,
`production_round_handoff`, `alerts_test`) are not offered until config.json allows them by name:

```json
"mcp": {"allow_writes": ["ledger_note_add"]}
```

## The skills

| Skill | Plugin | For requests like | Main commands |
|---|---|---|---|
| `industry-jobs` | industry | "plan the next jobs", "what can I install now", "did I miss anything" | `job-board`: slots, ready jobs, idle blueprints by what they can do, measured times and window-sized runs, a pool check, inputs per facility |
| `supply-run` | industry | "plan a supply run", "what do I need from the hub" | `restock` (needs vs stock, cargo fit, Multibuy) |
| `ops-check` | core | "anything need attention?" | the above, plus `ledger` |

A plugin ships its skills in its package (`Plugin.skills`), so installing a plugin adds its own.

The skills hold what is true for any operation. An installation supplies three things of its own:

- **`operation.json`** (`neworks operation template` prints a starting point): sites, the facility of
  each activity, sellers, planning time zone and finish windows, products never to build, items never to
  sell. The commands read everything about the operation from it, or from flags.
- **A playbook** (`neworks operation playbook`): Markdown files in `<config dir>/playbook/`, one per
  skill, with the operation's own rules - which lines, who builds what, decisions and their reasons. A
  skill reads its playbook first, and **playbook rules win over the skill's defaults**. Each skill folder
  has a `playbook.example.md` with the usual headings.
- **Its own `AGENTS.md`** (or `CLAUDE.md`) for the agent: [AGENTS.example.md](AGENTS.example.md)
  is a neutral starting point.

A rule that holds for every operation goes into the skill through a pull request; one about your
operation goes into your playbook. For scripting against the CLI's JSON or the ledger directly,
[agent-reference.md](agent-reference.md) has the `--json` shapes, the ledger tables and their
traps.
