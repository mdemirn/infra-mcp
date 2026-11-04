"""Load and validate the gateway's TOML config."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "gateway.toml"

# Backends start with mcp's minimal default environment plus this PATH, so a
# launcher with a thin PATH (Claude Desktop) still finds node, security, osascript.
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _expand(value: str) -> str:
    # Also expand "--flag=~/path": ssh-mcp only accepts --config=<path>.
    if value.startswith("-") and "=~" in value:
        flag, _, path = value.partition("=")
        return f"{flag}={_expand(path)}"
    return os.path.expandvars(os.path.expanduser(value))


@dataclass(frozen=True)
class Backend:
    name: str
    prefix: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    # The backend's tools already start with "<prefix>_" (win-mcp-server), so it
    # is mounted without a namespace instead of becoming "win_win_...".
    native_prefix: bool = False
    read_only: bool = False
    # Original tool names that stay usable in read-only mode even without readOnlyHint.
    read_only_tools: list[str] = field(default_factory=list)
    disabled_tools: list[str] = field(default_factory=list)
    # Argument names that carry a target host, and the hosts they may name.
    host_args: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)

    def public_name(self, tool: str) -> str:
        return tool if self.native_prefix else f"{self.prefix}_{tool}"

    def original_name(self, public: str) -> str:
        return public if self.native_prefix else public[len(self.prefix) + 1 :]


@dataclass(frozen=True)
class GatewayConfig:
    name: str
    audit_log: Path
    log_dir: Path
    read_only: bool
    backends: list[Backend]


def load_config(path: str | Path | None = None) -> GatewayConfig:
    path = Path(_expand(str(path))) if path else DEFAULT_CONFIG
    raw = tomllib.loads(path.read_text())
    gw = raw.get("gateway", {})

    backends: list[Backend] = []
    for item in raw.get("backends", []):
        if not item.pop("enabled", True):
            continue
        for key in ("command", "cwd", "url"):
            if item.get(key):
                item[key] = _expand(item[key])
        item["args"] = [_expand(a) for a in item.get("args", [])]
        item["env"] = {"PATH": DEFAULT_PATH, **{k: _expand(v) for k, v in item.get("env", {}).items()}}
        item["allowed_hosts"] = [h.lower() for h in item.get("allowed_hosts", [])]
        backend = Backend(**item)
        if bool(backend.command) == bool(backend.url):
            raise ValueError(f"backend {backend.name!r}: set exactly one of command or url")
        if backend.host_args and not backend.allowed_hosts:
            raise ValueError(f"backend {backend.name!r}: host_args needs allowed_hosts")
        backends.append(backend)

    prefixes = [b.prefix for b in backends]
    if len(set(prefixes)) != len(prefixes):
        raise ValueError(f"duplicate backend prefixes: {prefixes}")

    read_only = bool(gw.get("read_only", False)) or os.environ.get("INFRA_MCP_READ_ONLY", "") in ("1", "true", "yes")
    return GatewayConfig(
        name=gw.get("name", "infra"),
        audit_log=Path(_expand(gw.get("audit_log", "~/infra-mcp/logs/audit.jsonl"))),
        log_dir=Path(_expand(gw.get("log_dir", "~/infra-mcp/logs"))),
        read_only=read_only,
        backends=backends,
    )
