"""Browser navigation via the @agent360/browser-mcp server (MCP over stdio).

Used where a real browser beats raw fetching: About/Contact navigation in the
enrichment agent and the self-heal browser-assisted path. Opt-in via
BROWSER_MCP_ENABLED (requires Node.js for `npx @agent360/browser-mcp`).
"""

import shlex
import sys
from contextlib import asynccontextmanager

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

_NAVIGATE_HINTS = ("navigate", "goto", "open_url", "visit")
_CONTENT_HINTS = ("content", "snapshot", "text", "html", "read")


def enabled() -> bool:
    return get_settings().browser_mcp_enabled


@asynccontextmanager
async def browser_session():
    """Spawn the browser-mcp server and yield an initialized MCP session."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parts = shlex.split(get_settings().browser_mcp_command)
    command = parts[0]
    if sys.platform == "win32" and command == "npx":
        command = "npx.cmd"

    params = StdioServerParameters(command=command, args=parts[1:])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def _pick_tool(tools: list, hints: tuple[str, ...]) -> str | None:
    for hint in hints:
        for tool in tools:
            if hint in tool.name.lower():
                return tool.name
    return None


async def navigate_and_read(url: str, timeout_note: str = "") -> str:
    """Navigate to a URL in the MCP-driven browser and return page text/snapshot."""
    async with browser_session() as session:
        listing = await session.list_tools()
        tools = listing.tools
        nav = _pick_tool(tools, _NAVIGATE_HINTS)
        reader = _pick_tool(tools, _CONTENT_HINTS)
        if nav is None:
            raise RuntimeError(
                f"browser-mcp exposes no navigation tool (saw: {[t.name for t in tools][:10]})"
            )

        await session.call_tool(nav, {"url": url})
        if reader is None:
            return ""
        result = await session.call_tool(reader, {})
        chunks = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                chunks.append(text)
        return "\n".join(chunks)
