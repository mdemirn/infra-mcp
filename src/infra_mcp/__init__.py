"""infra-mcp: one MCP endpoint in front of several infrastructure MCP servers."""

import os

# Set before fastmcp is imported: no PyPI update check on every gateway start.
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
