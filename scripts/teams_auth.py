"""One-time interactive sign-in for the teams-mcp server → durable token.

Run:  uv run python scripts/teams_auth.py
It prints a Microsoft sign-in URL, runs a local callback listener, exchanges the
authorization code, and persists the refresh token to TEAMS_TOKEN_PATH. After
this, the backend (TeamsMcpAdapter) auto-refreshes from that token — no repeat
sign-in — so it stays validated indefinitely as long as the refresh token is
exercised (the poll loop does so continuously).
"""

import asyncio
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from mcp import ClientSession  # noqa: E402
from mcp.client.auth import OAuthClientProvider  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402
from mcp.shared.auth import OAuthClientMetadata  # noqa: E402

from app.channels.teams_token_store import FileTokenStorage  # noqa: E402
from app.config.settings import get_settings  # noqa: E402

_result: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        _result["code"] = qs.get("code", [None])[0]
        _result["state"] = qs.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>teams-mcp sign-in complete.</h2>"
                         b"<p>You can close this tab and return to the terminal.</p>")

    def log_message(self, *_):  # silence
        return


async def main() -> None:
    s = get_settings()
    if not s.teams_mcp_url:
        print("TEAMS_MCP_URL is not set in .env")
        return
    port = s.teams_auth_callback_port
    redirect_uri = f"http://localhost:{port}/callback"
    storage = FileTokenStorage()

    server = HTTPServer(("localhost", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def redirect_handler(url: str) -> None:
        print("\n" + "=" * 72)
        print("OPEN THIS URL TO SIGN IN (Microsoft):\n")
        print(url)
        print("=" * 72 + "\n")
        with_suppress = webbrowser.open(url)
        if not with_suppress:
            print("(couldn't auto-open a browser — copy the URL above)")

    async def callback_handler() -> tuple[str, str | None]:
        for _ in range(600):  # up to ~5 min
            if _result.get("code"):
                return _result["code"], _result.get("state")
            await asyncio.sleep(0.5)
        raise TimeoutError("no OAuth callback received within 5 minutes")

    provider = OAuthClientProvider(
        server_url=s.teams_mcp_url,
        client_metadata=OAuthClientMetadata(
            client_name="PR Intelligence Agent",
            redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    print(f"Connecting to {s.teams_mcp_url} (callback on {redirect_uri}) ...")
    async with streamablehttp_client(s.teams_mcp_url, auth=provider) as (read, write, *_), \
            ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print(f"\n[ok] Authenticated. teams-mcp exposed {len(tools.tools)} tools.")
        print(f"Durable token saved to: {storage._path}")
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
