# infra-mcp

**Talk to your firewall, hypervisor, switches, Windows and Linux servers from one MCP
entry, with an audit trail and guardrails in front of every call.**

infra-mcp is a small [FastMCP](https://github.com/jlowin/fastmcp) gateway. It mounts
existing MCP servers for FortiGate, vCenter, Ruijie switches, Windows (WinRM) and Linux
(SSH) under tool-name prefixes, so an assistant such as Claude can follow a question
across all of them in one conversation. The gateway adds a JSONL audit log, host
allowlists, a read-only switch and per-tool blocking; every tool itself comes from a
backend.

**203 tools · 5 backends · 1 MCP entry** (with the backend versions listed below)

```
                    ┌──────────────── infra-mcp gateway ────────────────┐
 Claude Code  ──▶   │ audit log ─▶ policy (read-only, disabled tools,    │ ──▶ fgt_*  FortiGate MCP     (stdio)
 Claude Desktop     │              host allowlist) ─▶ prefix router     │ ──▶ vc_*   vCenter MCP       (HTTP)
 any MCP client     │                                                    │ ──▶ sw_*   Ruijie switch MCP (stdio)
                    │ infra_status                                       │ ──▶ win_*  Windows MCP       (stdio)
                    └────────────────────────────────────────────────────┘ ──▶ lnx_*  ssh-mcp           (stdio)
```

**Contents:** [What you can ask](#what-you-can-ask) ·
[Capabilities](#capabilities) · [Guardrails in action](#guardrails-in-action) ·
[Installation](#installation) · [Configuration reference](#configuration-reference) ·
[Operations](#operations)

## What you can ask

Plain-language requests that span several systems. The assistant picks the tools; the
tool chains below show what it typically calls.

| You ask | The assistant combines |
|---|---|
| "Who is 192.0.2.45, and what can it reach?" | `fgt_list_dhcp_leases`, `fgt_list_arp_table` → `vc_list_vms`, `vc_get_vm_networks_pyvmomi` → `sw_telnet_execute` (`show mac-address-table`) → `fgt_lookup_policy` |
| "Why can't web01 reach the database on port 5432?" | `fgt_lookup_policy`, `fgt_query_traffic_log` (denied flows) → `lnx_read-command` on db01 (`ss -tlnp`, `journalctl -u postgresql`) |
| "Morning health check, please." | `fgt_get_system_resources`, `fgt_list_ipsec_tunnels` → `vc_get_resource_utilization_summary`, `vc_get_datastore_usage` → `win_get_disk_space`, `win_get_running_services` → `lnx_read-command` (`df -h`), `lnx_run-command` (`systemctl --failed`) |
| "Prepare db01 for tonight's maintenance." | `fgt_backup_config` → `vc_create_vm_snapshot` → `lnx_privileged-command` (`apt-get -s upgrade`, asks you first) |
| "Which VMs still have snapshots, and how old are they?" | `vc_list_vms` → `vc_list_vm_snapshots` |
| "Which switch port is the printer on, and is the port up?" | `fgt_list_dhcp_leases` → `sw_telnet_connect`, `sw_telnet_execute` (`show mac-address-table`, `show interfaces status`) |
| "When did fs01 last reboot, and is the backup service running?" | `win_execute_powershell` (`Get-CimInstance Win32_OperatingSystem`), `win_get_running_services` |
| "Who logged in to the firewall admin GUI today, and from where?" | `fgt_query_event_log` |
| "Which IPsec tunnels are down, and since when?" | `fgt_list_ipsec_tunnels`, `fgt_query_event_log` |
| "Publish the new web server on HTTPS." | `fgt_create_address_object`, `fgt_create_virtual_ip`, `fgt_create_firewall_policy`, only when writes are allowed |
| "Copy nginx.conf from web01 so I can review it." | `lnx_sftp-download` |
| "Which VMs use the most CPU, and what is running inside them?" | `vc_get_vms_with_high_resource_usage`, `vc_get_vm_performance_info` → `lnx_read-command` (`top -bn1`) |

### Example session

Illustrative data from a lab, abbreviated:

> **You:** Who is 192.0.2.45, and what can it reach?
>
> **Assistant:** *(calls `fgt_list_dhcp_leases`, `vc_list_vms`, `vc_get_vm_networks_pyvmomi`,
> `sw_telnet_execute`, `fgt_lookup_policy`)*
>
> 192.0.2.45 is **app02**, a VM on esx02 in port group `VLAN20-Servers`, MAC
> `00:50:56:aa:bb:cc`. The core switch learned that MAC on uplink `Gi0/48` (the ESXi
> host), so the traffic stays inside VLAN 20 until the firewall. On the FortiGate,
> policy 14 `servers-to-internet` allows it HTTPS and DNS outbound; no policy allows
> inbound connections from the internet.
>
> **You:** Snapshot it before I patch it.
>
> **Assistant:** *(calls `vc_create_vm_snapshot`)* Snapshot `before-patch` created on app02.

Every one of those calls is a line in the audit log.

## Capabilities

| Prefix | Backend | Tools | Covers |
|---|---|---|---|
| `fgt_` | [mdemirn/fortigate-mcp](https://github.com/mdemirn/fortigate-mcp) | 137 | Policies, objects, routing, DHCP, VPN, users, security profiles, sessions, logs, wireless, generic FortiOS REST access, several FortiGates at once |
| `vc_` | [giuliolibrando/vmware-vsphere-mcp-server](https://github.com/giuliolibrando/vmware-vsphere-mcp-server) | 41 | Inventory, VM details, performance and capacity, power, snapshots, clones, resources |
| `sw_` | [rmbnetlife/ruijie-cli-mcp](https://github.com/rmbnetlife/ruijie-cli-mcp) | 4 | Telnet CLI sessions on Ruijie switches and routers |
| `win_` | [InfraMCP/win-mcp-server](https://github.com/InfraMCP/win-mcp-server) | 6 | System info, services, disks and PowerShell over WinRM |
| `lnx_` | [tufantunc/ssh-mcp](https://github.com/tufantunc/ssh-mcp) 2.19.1 | 14 | Commands, sudo with approval, interactive and background sessions, signals, SFTP |
| `infra_status` | the gateway | 1 | Mounted backends, prefixes, read-only state, allowlists |

Tool lists come from the backends; a newer backend release may add or rename tools.
Any other MCP server reachable over stdio or streamable HTTP can be added as a backend.

<details>
<summary><b>FortiGate</b> (<code>fgt_</code>, 137 tools)</summary>

| Area | Tools |
|---|---|
| Devices | `fgt_list_devices`, `fgt_get_device_status`, `fgt_test_device_connection`, `fgt_add_device`, `fgt_remove_device`, `fgt_discover_vdoms`, `fgt_health_check`, `fgt_get_server_info`, `fgt_get_system_resources`, `fgt_get_performance_status`, `fgt_get_system_time`, `fgt_list_firmware`, `fgt_get_license_status`, `fgt_get_ha_status`, `fgt_get_security_rating`, `fgt_list_running_processes` |
| Firewall policies | `fgt_list_firewall_policies`, `fgt_get_firewall_policy_detail` (resolves groups, identity and profiles), `fgt_create_firewall_policy`, `fgt_update_firewall_policy`, `fgt_move_firewall_policy`, `fgt_delete_firewall_policy`, `fgt_lookup_policy` (which rule matches a flow), `fgt_list_central_snat_rules`, `fgt_list_shaping_policies`, `fgt_list_traffic_shapers`, `fgt_list_recurring_schedules`, `fgt_list_onetime_schedules`, `fgt_list_internet_service_names` |
| Addresses | `fgt_list_address_objects`, `fgt_create_address_object`, `fgt_update_address_object`, `fgt_delete_address_object` |
| Address groups | `fgt_list_address_groups`, `fgt_get_address_group_detail`, `fgt_create_address_group`, `fgt_add_address_group_members`, `fgt_remove_address_group_members`, `fgt_delete_address_group` |
| Services | `fgt_list_service_objects`, `fgt_create_service_object`, `fgt_update_service_object`, `fgt_delete_service_object`, `fgt_list_service_groups` |
| VIPs and pools | `fgt_list_virtual_ips`, `fgt_get_virtual_ip_detail`, `fgt_create_virtual_ip`, `fgt_update_virtual_ip`, `fgt_delete_virtual_ip`, `fgt_list_vip_groups`, `fgt_list_ip_pools` |
| Network and routing | `fgt_list_interfaces`, `fgt_get_interface_status`, `fgt_list_interface_counters`, `fgt_list_zones`, `fgt_list_static_routes`, `fgt_get_static_route_detail`, `fgt_create_static_route`, `fgt_update_static_route`, `fgt_delete_static_route`, `fgt_get_routing_table`, `fgt_list_ipv6_routes`, `fgt_lookup_route`, `fgt_list_policy_routes`, `fgt_get_bgp_config`, `fgt_get_ospf_config`, `fgt_list_prefix_lists`, `fgt_list_route_maps`, `fgt_get_sdwan_config`, `fgt_list_sdwan_health_checks`, `fgt_list_sdwan_members`, `fgt_list_arp_table`, `fgt_list_lldp_neighbors`, `fgt_ping_from_device`, `fgt_geoip_lookup` |
| DHCP | `fgt_list_dhcp_servers` (with reservations), `fgt_list_dhcp_leases`, `fgt_create_dhcp_reservation`, `fgt_delete_dhcp_reservation` |
| VPN | `fgt_list_ipsec_tunnels` (live status), `fgt_list_ipsec_phase1`, `fgt_list_ipsec_phase2`, `fgt_execute_ipsec_tunnel_action` (up/down), `fgt_list_ssl_vpn_sessions`, `fgt_get_ssl_vpn_settings`, `fgt_list_ssl_vpn_portals`, `fgt_list_local_certificates`, `fgt_list_ca_certificates`, `fgt_list_remote_certificates` |
| Users and authentication | `fgt_list_local_users`, `fgt_list_user_groups`, `fgt_list_firewall_auth_sessions`, `fgt_list_radius_servers`, `fgt_list_ldap_servers`, `fgt_list_tacacs_servers`, `fgt_list_saml_servers`, `fgt_list_fsso_agents`, `fgt_list_fortitokens`, `fgt_list_admins`, `fgt_list_api_users`, `fgt_list_endpoint_devices` |
| Security profiles | `fgt_list_security_profiles` and `fgt_get_security_profile_detail` for application control, web filter, antivirus, IPS, DNS filter, SSL/SSH inspection, DLP, email filter, file filter, ICAP, SSH filter, WAF, VoIP and virtual patching; `fgt_list_url_filters`, `fgt_list_dns_domain_filters`, `fgt_list_ips_custom_signatures`, `fgt_list_application_groups` |
| Sessions and logs | `fgt_list_active_sessions`, `fgt_get_fortiview_top`, `fgt_query_traffic_log`, `fgt_query_event_log`, `fgt_query_utm_log`, `fgt_get_log_settings`, `fgt_list_syslog_settings`, `fgt_get_fortianalyzer_settings`, `fgt_backup_config` |
| System settings | `fgt_get_system_global`, `fgt_get_dns_settings`, `fgt_get_ntp_settings`, `fgt_list_snmp_communities`, `fgt_get_snmp_sysinfo` |
| Wireless and switching | `fgt_list_wireless_ssids`, `fgt_list_ap_profiles`, `fgt_list_managed_aps`, `fgt_list_wifi_clients`, `fgt_list_rogue_aps`, `fgt_get_wifi_controller_settings`, `fgt_list_managed_switches`, `fgt_list_switch_port_stats` |
| Whole REST API | `fgt_cmdb_list`, `fgt_cmdb_get`, `fgt_create_cmdb_object`, `fgt_update_cmdb_object`, `fgt_delete_cmdb_object`, `fgt_monitor_get`, `fgt_execute_monitor_action`, `fgt_log_get` |

The generic tools reach any FortiOS table or monitor endpoint:

```
fgt_cmdb_list   path="firewall/policy" filter_expr="action==deny" format_fields="policyid|name|srcaddr"
fgt_monitor_get path="system/interface" params={"interface_name": "wan1"}
fgt_log_get     path="memory/event/vpn" rows=20 filter_expr="logdesc=@tunnel"
```

fortigate-mcp has its own write gate on top of the gateway's: create, update, delete,
move, add, remove and execute tools are refused until `allow_writes` is enabled in its
configuration.

</details>

<details>
<summary><b>vCenter</b> (<code>vc_</code>, 41 tools)</summary>

| Area | Tools |
|---|---|
| Inventory | `vc_list_datacenters`, `vc_list_datacenters_pyvmomi`, `vc_list_clusters_pyvmomi`, `vc_list_hosts`, `vc_list_folders`, `vc_list_datastores`, `vc_list_networks`, `vc_list_vlans`, `vc_list_templates`, `vc_list_vms`, `vc_find_template_pyvmomi`, `vc_get_datacenter_details`, `vc_get_folder_details`, `vc_get_host_details`, `vc_get_datastore_details`, `vc_get_network_details`, `vc_get_port_groups`, `vc_get_vlan_info` |
| Virtual machines | `vc_get_vm_details`, `vc_get_vm_networks_pyvmomi`, `vc_get_vm_storage_info`, `vc_get_vm_disk_usage`, `vc_get_vm_events`, `vc_list_vm_snapshots`, `vc_generate_vm_report` |
| Performance and capacity | `vc_get_vm_performance_info`, `vc_get_host_performance_info`, `vc_get_resource_utilization_summary`, `vc_get_datastore_usage`, `vc_get_vms_with_high_resource_usage`, `vc_get_alarms` |
| Changes | `vc_power_on_vm`, `vc_power_off_vm`, `vc_force_power_off_vm`, `vc_bulk_power_operations`, `vc_create_vm_snapshot`, `vc_delete_vm_snapshot`, `vc_clone_vm`, `vc_modify_vm_resources`, `vc_delete_vm`, `vc_bulk_delete_vms` |

Example requests: "List the VMs on esx02 that are powered off", "How full is each
datastore?", "Clone the web template into web03", "Give app02 two more vCPUs".
`vc_bulk_delete_vms` and `vc_delete_vm` are good candidates for `disabled_tools`.

</details>

<details>
<summary><b>Ruijie switches</b> (<code>sw_</code>, 4 tools)</summary>

| Tool | Does |
|---|---|
| `sw_telnet_connect` | Opens a Telnet session to a switch or router and returns a session ID and the CLI mode |
| `sw_telnet_execute` | Runs a CLI command in that session; waits for the prompt and returns the output |
| `sw_telnet_list_sessions` | Lists the open sessions |
| `sw_telnet_disconnect` | Closes a session |

Example requests: "Show the interface status on the core switch", "Find MAC
`0050.56aa.bbcc` in the MAC table", "Which VLANs are on port Gi0/12?". The host
allowlist applies to `sw_telnet_connect`, so the assistant can only open sessions to the
switches you listed.

</details>

<details>
<summary><b>Windows</b> (<code>win_</code>, 6 tools)</summary>

| Tool | Does |
|---|---|
| `win_get_system_info` | Basic system information |
| `win_get_running_services` | Running services |
| `win_get_disk_space` | Disk space per drive |
| `win_execute_powershell` | Runs a PowerShell command on the host |
| `win_setup_credentials` | Asks for the WinRM credentials of a host through a GUI prompt |
| `win_clear_credentials` | Clears the cached credentials of a host |

Example requests: "How much space is left on fs01's D: drive?", "Which automatic
services are stopped on dc01?" (PowerShell), "Show the last ten system log errors on
fs01". The
example configuration keeps the three read-only tools available in read-only mode via
`read_only_tools`.

</details>

<details>
<summary><b>Linux</b> (<code>lnx_</code>, 14 tools)</summary>

| Tool | Does |
|---|---|
| `lnx_list-connections` | Configured SSH profiles and their connection state |
| `lnx_read-command` | Read-only commands from ssh-mcp's allowlist (`ls`, `cat`, `grep`, `df`, `ps`, `ss`, `journalctl`, `tail`, ...) |
| `lnx_run-command` | Any command; destructive ones go through the approval prompt |
| `lnx_privileged-command` | Command with `sudo`, always approved by you first |
| `lnx_open-session`, `lnx_list-sessions`, `lnx_read-session-output`, `lnx_close-session` | Interactive shells (working directory and environment persist) and background jobs |
| `lnx_signal-process` | Sends INT, TERM or KILL to a process |
| `lnx_sftp-list`, `lnx_sftp-download`, `lnx_sftp-upload` | Browse files and move short text files through the conversation |
| `lnx_sftp-download-file`, `lnx_sftp-upload-file` | Stream binary or large files to and from `transferRoot` without passing them through the model |

Example requests: "Which services failed on db01?", "Tail the nginx error log on
web01", "Run the backup script on db01 in the background and tell me when it finishes",
"Update the packages on web01" (you approve the `sudo` step in the client).

</details>

## Guardrails in action

The gateway checks every call before it reaches a backend. These are the messages the
assistant gets back:

| Situation | Response |
|---|---|
| Host outside the allowlist | `win_execute_powershell: host 'laptop-17' is not in the allowed_hosts list for backend 'windows'` |
| Read-only mode | `vc_power_off_vm is blocked: backend 'vcenter' is in read-only mode` |
| Disabled tool | `vc_bulk_delete_vms is disabled in the infra gateway config` |
| `sudo` or destructive Linux command | ssh-mcp shows an approval prompt in the client; nothing runs until you accept |

In read-only mode the blocked tools are also removed from the tool list, so the
assistant does not try them in the first place. The allowlist check only applies when a
host argument is given; a vCenter call without `hostname` goes to the server the backend
is configured for.

Each call adds one line to the audit log. Secret-looking arguments are masked and
denials are recorded with their reason:

```json
{"ts": "2026-11-04T07:58:12+00:00", "tool": "lnx_read-command", "backend": "linux", "args": {"profile": "db01", "command": "df -h"}, "outcome": "ok", "ms": 412}
{"ts": "2026-11-04T07:59:40+00:00", "tool": "sw_telnet_connect", "backend": "ruijie", "args": {"host": "192.0.2.2", "port": 23, "username": "admin", "password": "***"}, "outcome": "ok", "ms": 836}
{"ts": "2026-11-04T08:01:05+00:00", "tool": "win_execute_powershell", "backend": "windows", "args": {"hostname": "laptop-17", "command": "Get-Service"}, "outcome": "denied", "error": "win_execute_powershell: host 'laptop-17' is not in the allowed_hosts list for backend 'windows'", "ms": 0}
```

`infra_status` shows what is mounted and how it is restricted. With the example
configuration:

```json
{
  "read_only_global": false,
  "audit_log": "/Users/you/infra-mcp/logs/audit.jsonl",
  "backends": [
    {
      "name": "fortigate",
      "prefix": "fgt_",
      "target": "/Users/you/fortigate-mcp/.venv/bin/python",
      "read_only": false,
      "disabled_tools": [],
      "allowed_hosts": []
    },
    {
      "name": "vcenter",
      "prefix": "vc_",
      "target": "http://127.0.0.1:8765/mcp",
      "read_only": false,
      "disabled_tools": [],
      "allowed_hosts": ["vcenter.example.internal", "192.0.2.20"]
    }
  ]
}
```

(abbreviated: `ruijie`, `windows` and `linux` follow.)

Other properties:

- A backend that fails to start is logged and skipped; the others keep working.
- Each stdio backend's stderr goes to `logs/<backend>.log`.
- The audit file is created with mode 0600; strings longer than 400 characters are
  truncated in it.

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
into `config/ssh-mcp.toml`. See the [configuration reference](#configuration-reference).

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

## Configuration reference

`config/gateway.toml`, section `[gateway]`:

| Key | Meaning |
|---|---|
| `name` | Server name shown to the client (`infra`) |
| `audit_log` | JSONL audit file |
| `log_dir` | Directory for the backends' stderr logs |
| `read_only` | `true` hides and blocks every write tool |

Each `[[backends]]` block:

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
`INFRA_MCP_CONFIG`. `INFRA_MCP_READ_ONLY=1` turns on read-only mode regardless of the
file.

`config/ssh-mcp.toml` holds the Linux profiles (`name`, `host`, `user`, `auth`,
`keyRef`, `group`, `role`), the approval mode, timeouts, a daily command quota and the
optional `transferRoot` for file transfers; see `config/ssh-mcp.example.toml`.

## Operations

- **Add a Linux server:** add a `[[profiles]]` block to `config/ssh-mcp.toml` and restart
  the client.
- **Allow another Windows host, vCenter or switch:** add it to that backend's
  `allowed_hosts`, with every name and address the assistant may use for it.
- **Block a dangerous tool:** add its original name to that backend's `disabled_tools`.
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
