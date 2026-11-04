"""Build the infra gateway: every backend MCP server mounted under its own prefix."""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.server import create_proxy
from mcp.types import ToolAnnotations

from .config import Backend, GatewayConfig, load_config
from .policy import AuditMiddleware, Policy, PolicyMiddleware

INSTRUCTIONS = """Single gateway for the infrastructure. Tool name prefixes:
{prefixes}
Every call is written to the gateway audit log. Write tools act on PRODUCTION systems."""


def _transport(backend: Backend, cfg: GatewayConfig) -> Any:
    if backend.url:
        return StreamableHttpTransport(backend.url)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    return StdioTransport(
        command=backend.command,
        args=backend.args,
        env=backend.env,
        cwd=backend.cwd,
        keep_alive=True,
        log_file=cfg.log_dir / f"{backend.name}.log",
    )


def build_server(cfg: GatewayConfig, targets: dict[str, Any] | None = None) -> FastMCP:
    """`targets` replaces a backend's transport by name (tests pass in-memory servers)."""
    targets = targets or {}
    prefixes = "\n".join(f"- {b.prefix}_* : {b.name}" for b in cfg.backends)
    gateway = FastMCP(cfg.name, instructions=INSTRUCTIONS.format(prefixes=prefixes))
    policy = Policy(cfg)

    for backend in cfg.backends:
        proxy = create_proxy(targets.get(backend.name) or _transport(backend, cfg), name=backend.name)
        gateway.mount(proxy, namespace=None if backend.native_prefix else backend.prefix)

    @gateway.tool(name="infra_status", annotations=ToolAnnotations(readOnlyHint=True))
    def infra_status() -> dict[str, Any]:
        """Show the gateway's backends, their prefixes, read-only state and host allowlists."""
        return {
            "read_only_global": cfg.read_only,
            "audit_log": str(cfg.audit_log),
            "backends": [
                {
                    "name": b.name,
                    "prefix": f"{b.prefix}_",
                    "target": b.url or b.command,
                    "read_only": policy.is_read_only(b),
                    "disabled_tools": b.disabled_tools,
                    "allowed_hosts": b.allowed_hosts,
                }
                for b in cfg.backends
            ],
        }

    # Audit comes before policy so it is the outer layer and also records denials.
    gateway.add_middleware(AuditMiddleware(cfg.audit_log, policy))
    gateway.add_middleware(PolicyMiddleware(policy))
    return gateway


def main() -> None:
    build_server(load_config(os.environ.get("INFRA_MCP_CONFIG"))).run(show_banner=False)


if __name__ == "__main__":
    main()
