# infra-mcp

One MCP entry for several infrastructure MCP servers. infra-mcp is a small
[FastMCP](https://github.com/jlowin/fastmcp) gateway that mounts existing MCP servers
for FortiGate, vCenter, Ruijie switches, Windows (WinRM) and Linux (SSH) under tool-name
prefixes, and puts an audit log, a host allowlist and a read-only switch in front of all
of them. It has no management code of its own: every tool comes from a backend.

```
                    ┌──────────────── infra-mcp gateway ────────────────┐
 Claude Code  ──▶   │ audit log ─▶ policy (read-only, disabled tools,    │ ──▶ fgt_*  FortiGate MCP     (stdio)
 Claude Desktop     │              host allowlist) ─▶ prefix router     │ ──▶ vc_*   vCenter MCP       (HTTP)
 any MCP client     │                                                    │ ──▶ sw_*   Ruijie switch MCP (stdio)
                    │ infra_status                                       │ ──▶ win_*  Windows MCP       (stdio)
                    └────────────────────────────────────────────────────┘ ──▶ lnx_*  ssh-mcp           (stdio)
```

## Features

- **One registration.** The client sees one server; tools are named `<prefix>_<tool>`
  (`fgt_list_firewall_policies`, `lnx_run-command`, ...). Any MCP server reachable over
  stdio or streamable HTTP can be added as a backend.
- **Audit log.** Every tool call is appended to a JSONL file (mode 0600): timestamp,
  tool, backend, arguments, outcome (`ok`, `error`, `denied`) and duration. Arguments
  whose names look like secrets (`pass`, `pwd`, `secret`, `token`, `credential`,
  `api_key`) are written as `***`; long strings are cut at 400 characters. Each stdio
  backend's stderr goes to `logs/<backend>.log`.
- **Host allowlist.** For backends that take a target host as an argument, calls to any
  host outside `allowed_hosts` are refused before they reach the backend.
- **Read-only mode.** Globally (`read_only = true` or `INFRA_MCP_READ_ONLY=1`) or per
  backend. Tools without `readOnlyHint` are hidden from the tool list and refused when
  called by name; `read_only_tools` lists exceptions for backends that do not annotate
  their tools.
- **Disabled tools.** `disabled_tools` removes individual backend tools completely.
- **Approval prompts pass through.** ssh-mcp asks for confirmation of `sudo` and
  destructive commands through MCP elicitation; the gateway forwards those prompts to
  the client.
- **Fault isolation.** A backend that fails to start is logged and skipped; the others
  keep working.
- `infra_status` shows the mounted backends, their prefixes, read-only state and
  allowlists.

## Backends

| Prefix | Backend | Transport |
|---|---|---|
| `fgt_` | [mdemirn/fortigate-mcp](https://github.com/mdemirn/fortigate-mcp) | stdio |
| `vc_` | [giuliolibrando/vmware-vsphere-mcp-server](https://github.com/giuliolibrando/vmware-vsphere-mcp-server) | streamable HTTP |
| `sw_` | [rmbnetlife/ruijie-cli-mcp](https://github.com/rmbnetlife/ruijie-cli-mcp) | stdio |
| `win_` | [InfraMCP/win-mcp-server](https://github.com/InfraMCP/win-mcp-server) | stdio, keeps its own `win_` names |
| `lnx_` | [tufantunc/ssh-mcp](https://github.com/tufantunc/ssh-mcp) 2.19.1 from npm | stdio |
| `infra_status` | the gateway itself | |

Install only the backends you need and remove the others from the configuration.

## Requirements

- Python 3.12 or later
- Node.js 20.6 or later for the Linux backend (ssh-mcp)
- The backend MCP servers you want to use, installed as described in their own READMEs
- An MCP client: Claude Code, Claude Desktop or any other client that supports stdio and
  elicitation

The defaults (the `PATH` given to backends, the Claude Desktop helper script) assume macOS
with Homebrew; on Linux, adjust the paths in the configuration.

## Installation

### 1. Get the gateway

The example configuration assumes the repository lives in `~/infra-mcp`:

```bash
git clone https://github.com/mdemirn/infra-mcp.git ~/infra-mcp
cd ~/infra-mcp
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

With uv: `uv venv --python 3.12 && uv pip install -e '.[dev]'`. Install in editable mode
(`-e`): the gateway looks for `config/gateway.toml` next to the source tree.

### 2. Install the Linux backend (optional)

```bash
cd backends/ssh-mcp
npm ci              # installs the pinned ssh-mcp from package-lock.json
cd ../..
```

### 3. Install the other backends

Follow each backend's README. The example configuration expects:

| Backend | Location |
|---|---|
| FortiGate MCP | `~/fortigate-mcp` with its `.venv` and `config/config.json` |
| vCenter MCP | already running on `http://127.0.0.1:8765/mcp` |
| Ruijie MCP | `~/ruijie-cli-mcp/.venv/bin/ruijie-cli-mcp` |
| Windows MCP | `~/win-mcp-server/.venv/bin/win-mcp-server` |

Credentials stay with the backends: the FortiGate API tokens in the FortiGate MCP's
`config.json`, the vCenter login in that server's environment, WinRM credentials as
win-mcp-server handles them, SSH keys referenced from `config/ssh-mcp.toml`. The gateway
stores no secrets.

### 4. Configure

```bash
cp config/gateway.example.toml config/gateway.toml
cp config/ssh-mcp.example.toml config/ssh-mcp.toml
chmod 600 config/gateway.toml config/ssh-mcp.toml
```

Both files are ignored by git. Remove the backends you do not use (or add
`enabled = false`), then put your own hosts into `allowed_hosts` and your Linux servers
into `config/ssh-mcp.toml`.

`[gateway]`:

| Key | Meaning |
|---|---|
| `name` | Server name shown to the client (`infra`) |
| `audit_log` | JSONL audit file |
| `log_dir` | Directory for the backends' stderr logs |
| `read_only` | `true` hides and blocks every write tool |

`[[backends]]`:

| Key | Meaning |
|---|---|
| `name`, `prefix` | Backend name and the tool-name prefix (unique) |
| `command`, `args`, `cwd`, `env` | Start a stdio backend (`~` and `$VARS` are expanded, also in `--flag=~/path`) |
| `url` | Connect to a streamable HTTP backend instead (set either `command` or `url`) |
| `native_prefix` | The backend's tools already carry the prefix; mount them without another one |
| `host_args`, `allowed_hosts` | Argument names that carry a target host, and the hosts allowed there (case-insensitive) |
| `read_only` | Read-only mode for this backend only |
| `read_only_tools` | Tools that stay available in read-only mode although they lack `readOnlyHint` |
| `disabled_tools` | Tools that are never exposed (original backend names) |
| `enabled` | `false` skips the backend |

The gateway reads `config/gateway.toml` from the repository, or the file named in
`INFRA_MCP_CONFIG`.

For the Linux backend, create one dedicated account per server and a key for it:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_mcp -C infra-mcp
ssh-copy-id -i ~/.ssh/id_ed25519_mcp.pub mcp@<server>
```

Give that account `sudo` only where the assistant needs it; ssh-mcp still asks for
approval before each privileged or destructive command.

### 5. Check it

```bash
.venv/bin/python -m pytest -q
npx @modelcontextprotocol/inspector .venv/bin/infra-mcp
```

The tests use a fake stdio backend and touch no real system. In the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector), connect and run
`infra_status`: every configured backend should be listed and its tools should appear
with their prefixes.

### 6. Register it in your MCP client

Claude Code:

```bash
claude mcp add infra -s user -- ~/infra-mcp/.venv/bin/infra-mcp
claude mcp list
```

Add `-e INFRA_MCP_CONFIG=/path/to/gateway.toml` to use a configuration file elsewhere.

Claude Desktop: quit the app completely (Cmd+Q), then run

```bash
~/infra-mcp/scripts/use-infra-in-claude-desktop.sh fortigate ruijie-cli
```

The script adds the `infra` entry, removes the entries named as arguments (servers the
gateway now replaces), keeps a backup under `backups/`, and refuses to run while Claude
Desktop is open, because the app writes its in-memory settings back when it quits. To
register it by hand instead, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "infra": { "command": "/Users/<you>/infra-mcp/.venv/bin/infra-mcp" }
  }
}
```

## Operations

- **Add a Linux server:** add a `[[profiles]]` block to `config/ssh-mcp.toml` and restart
  the client.
- **Allow another Windows host, vCenter or switch:** add it to that backend's
  `allowed_hosts`, with every name and address the assistant may use for it.
- **Emergency brake:** set `read_only = true` in `[gateway]` (or start the gateway with
  `INFRA_MCP_READ_ONLY=1`) and restart the client.
- **Watch the audit log:** `tail -f ~/infra-mcp/logs/audit.jsonl`
- Backends are mounted once, when the gateway starts. If a backend was down at that
  moment, its tools stay missing until the client restarts the gateway.

## Why FastMCP 3 and MCP SDK v1

Newer Claude Code releases open the connection with a `server/discover` request for the
2026-07-28 protocol. A gateway on FastMCP 4 / MCP SDK v2 accepts it and locks the
session to that protocol, but the backends only speak the older initialize handshake:
their tools then fail with "Unknown tool" and ssh-mcp's approval prompts cannot be
forwarded. The v1 SDK rejects `server/discover`, Claude Code falls back to the
2025-11-25 handshake, and everything works
(`test_claude_code_opening_falls_back_to_the_handshake` replays the real frames).
The dependencies are pinned for this reason: `fastmcp==3.4.11`, `mcp<2`, and
`ssh-mcp@2.19.1` in `backends/ssh-mcp/package-lock.json`.

## Project layout

```
src/infra_mcp/
  gateway.py      builds the gateway: mounts each backend under its prefix, adds infra_status
  config.py       loads and validates config/gateway.toml
  policy.py       read-only mode, disabled tools, host allowlist, audit log middleware
config/           gateway.example.toml, ssh-mcp.example.toml (copy without .example)
backends/ssh-mcp/ npm package pinning ssh-mcp
scripts/          use-infra-in-claude-desktop.sh
tests/            gateway tests with a fake stdio backend
```
