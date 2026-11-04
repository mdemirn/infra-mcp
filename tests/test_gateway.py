import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from infra_mcp.config import load_config
from infra_mcp.gateway import build_server

FAKE = Path(__file__).with_name("fake_backend.py")


def write_config(tmp_path: Path, extra: str = "", gateway_extra: str = "") -> Path:
    cfg = tmp_path / "gateway.toml"
    cfg.write_text(f"""
[gateway]
audit_log = "{tmp_path}/audit.jsonl"
log_dir = "{tmp_path}"
{gateway_extra}

[[backends]]
name = "alpha"
prefix = "a"
command = "{sys.executable}"
args = ["{FAKE}"]
host_args = ["hostname"]
allowed_hosts = ["srv1", "10.0.0.1"]
disabled_tools = ["wipe"]
{extra}
""")
    return cfg


def run(coro):
    return asyncio.run(coro)


async def names(server) -> set[str]:
    async with Client(server) as c:
        return {t.name for t in await c.list_tools()}


def audit(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


def test_tools_are_prefixed_and_disabled_tools_hidden(tmp_path):
    server = build_server(load_config(write_config(tmp_path)))
    assert run(names(server)) == {"a_show", "a_change", "a_guarded", "infra_status"}


def test_native_prefix_keeps_backend_names(tmp_path):
    cfg = tmp_path / "gateway.toml"
    cfg.write_text(f"""
[gateway]
audit_log = "{tmp_path}/audit.jsonl"
log_dir = "{tmp_path}"
[[backends]]
name = "showy"
prefix = "show"
native_prefix = true
command = "{sys.executable}"
args = ["{FAKE}"]
""")
    assert "show" in run(names(build_server(load_config(cfg))))


def test_host_allowlist(tmp_path):
    server = build_server(load_config(write_config(tmp_path)))

    async def go():
        async with Client(server) as c:
            ok = await c.call_tool("a_change", {"hostname": "SRV1", "password": "hunter2"})
            assert ok.content[0].text == "changed SRV1"
            assert (await c.call_tool("a_show", {})).content[0].text == "shown"
            with pytest.raises(ToolError, match="allowed_hosts"):
                await c.call_tool("a_change", {"hostname": "evil.example"})

    run(go())
    rows = audit(tmp_path)
    assert [r["outcome"] for r in rows] == ["ok", "ok", "denied"]
    assert rows[0]["args"] == {"hostname": "SRV1", "password": "***"}
    assert rows[0]["backend"] == "alpha"


def test_disabled_tool_is_blocked_when_called_by_name(tmp_path):
    server = build_server(load_config(write_config(tmp_path)))

    async def go():
        async with Client(server) as c:
            with pytest.raises(ToolError, match="disabled"):
                await c.call_tool("a_wipe", {})

    run(go())


@pytest.mark.parametrize("how", ["backend", "global", "env"])
def test_read_only_mode(tmp_path, monkeypatch, how):
    extra = "read_only = true" if how == "backend" else ""
    gateway_extra = "read_only = true" if how == "global" else ""
    if how == "env":
        monkeypatch.setenv("INFRA_MCP_READ_ONLY", "1")
    server = build_server(load_config(write_config(tmp_path, extra, gateway_extra)))

    async def go():
        async with Client(server) as c:
            assert {t.name for t in await c.list_tools()} == {"a_show", "infra_status"}
            assert (await c.call_tool("a_show", {"hostname": "srv1"})).content[0].text == "shown srv1"
            with pytest.raises(ToolError, match="read-only"):
                await c.call_tool("a_change", {"hostname": "srv1"})

    run(go())


def test_read_only_tools_override(tmp_path):
    server = build_server(load_config(write_config(tmp_path, 'read_only = true\nread_only_tools = ["change"]')))
    assert run(names(server)) == {"a_show", "a_change", "infra_status"}


@pytest.mark.parametrize("action,expected", [("accept", "approved"), ("decline", "refused (decline)")])
def test_elicitation_is_forwarded_to_the_client(tmp_path, action, expected):
    """ssh-mcp asks for sudo/destructive approval via elicitation; it must reach the user."""
    server = build_server(load_config(write_config(tmp_path)))
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        if action == "accept":
            return response_type(approve=True)
        from fastmcp.client.elicitation import ElicitResult
        return ElicitResult(action="decline")

    async def go():
        async with Client(server, elicitation_handler=handler) as c:
            return (await c.call_tool("a_guarded", {})).content[0].text

    assert run(go()) == expected
    assert prompts == ["Run the guarded command?"]


# Claude Code 2.1.308's opening frames, captured with a probe server on 2026-10-26.
CLAUDE_CODE_DISCOVER = {
    "jsonrpc": "2.0", "id": "server-discover-probe-1", "method": "server/discover",
    "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "claude-code", "version": "2.1.308"},
        "io.modelcontextprotocol/clientCapabilities": {"roots": {"listChanged": True}, "elicitation": {"form": {}, "url": {}}},
    }},
}


def test_claude_code_opening_falls_back_to_the_handshake(tmp_path):
    """Discover must be refused (not answered as 2026-07-28), so Claude Code stays on the handshake
    era that the backends and ssh-mcp's elicitation approvals need."""
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-m", "infra_mcp.gateway"],
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "INFRA_MCP_CONFIG": str(write_config(tmp_path))},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )

    def rpc(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        if "id" not in msg:
            return None
        while True:
            reply = json.loads(proc.stdout.readline())
            if reply.get("id") == msg["id"]:
                return reply

    try:
        assert "error" in rpc(CLAUDE_CODE_DISCOVER)
        init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {"elicitation": {}},
            "clientInfo": {"name": "claude-code", "version": "2.1.308"}}})
        assert init["result"]["protocolVersion"] == "2025-11-25"
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert "a_show" in {t["name"] for t in tools["result"]["tools"]}
    finally:
        proc.kill()
        proc.wait()


def test_broken_backend_does_not_take_the_others_down(tmp_path):
    cfg = write_config(tmp_path, '\n[[backends]]\nname = "broken"\nprefix = "b"\ncommand = "/nonexistent/server"\n')
    server = build_server(load_config(cfg))

    async def go():
        async with Client(server) as c:
            listed = {t.name for t in await c.list_tools()}
            return listed, (await c.call_tool("a_show", {})).content[0].text

    listed, shown = run(go())
    assert "a_show" in listed and not any(n.startswith("b_") for n in listed)
    assert shown == "shown"


def test_config_rejects_duplicate_prefix(tmp_path):
    cfg = write_config(tmp_path, f'\n[[backends]]\nname = "beta"\nprefix = "a"\ncommand = "{sys.executable}"\n')
    with pytest.raises(ValueError, match="duplicate"):
        load_config(cfg)


def test_example_config_loads():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "gateway.example.toml")
    assert [b.prefix for b in cfg.backends] == ["fgt", "vc", "sw", "win", "lnx"]
    assert all(b.env["PATH"].startswith("/opt/homebrew/bin") for b in cfg.backends)
    linux = next(b for b in cfg.backends if b.name == "linux")
    assert all("~" not in a for a in linux.args)
