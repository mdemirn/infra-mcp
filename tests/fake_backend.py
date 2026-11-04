"""Stand-in backend for the tests, run over stdio like the real ones."""

from dataclasses import dataclass

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("fake")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def show(hostname: str = "") -> str:
    return f"shown {hostname}".strip()


@mcp.tool()
def change(hostname: str = "", password: str = "") -> str:
    return f"changed {hostname}".strip()


@mcp.tool()
def wipe() -> str:
    return "wiped"


@dataclass
class Approval:
    approve: bool


@mcp.tool()
async def guarded(ctx: Context) -> str:
    """Mimics ssh-mcp's approval prompt, which goes through MCP elicitation."""
    answer = await ctx.elicit("Run the guarded command?", response_type=Approval)
    if answer.action == "accept" and answer.data.approve:
        return "approved"
    return f"refused ({answer.action})"


if __name__ == "__main__":
    mcp.run()
