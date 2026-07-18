"""Teams + Graph mail via the user's production teams-mcp server (MCP client).

Reuses the deployed toolset: chat_message_send / channel_message_reply /
mail_reply for delivery, subscribe_to_mentions + get_pending_mentions for
inbound. Enabled only when TEAMS_MCP_URL is set (needs a durable OAuth token on
the server — see the plan's risks). Tool names are resolved dynamically so the
adapter tolerates server-side renames."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

from app.channels.base import ChannelAdapter, ChannelInbound, OutboundMessage
from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

# Serialize all teams-mcp calls process-wide: the OAuth provider rotates the
# refresh token on renewal, and concurrent renewals invalidate each other.
_TOKEN_LOCK = asyncio.Lock()


class TeamsMcpAdapter(ChannelAdapter):
    channel = "teams"

    def enabled(self) -> bool:
        # requires both the URL and a persisted durable token (run scripts/teams_auth.py)
        if not get_settings().teams_mcp_url:
            return False
        from app.channels.teams_token_store import FileTokenStorage

        return FileTokenStorage().has_credentials()

    def _auth_provider(self):
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata

        from app.channels.teams_token_store import FileTokenStorage

        s = get_settings()
        port = s.teams_auth_callback_port

        async def _no_interactive(_url: str) -> None:
            raise RuntimeError(
                "teams-mcp token missing/expired — run `uv run python scripts/teams_auth.py`"
            )

        async def _no_callback() -> tuple[str, str | None]:
            raise RuntimeError("teams-mcp requires interactive re-auth")

        return OAuthClientProvider(
            server_url=s.teams_mcp_url,
            client_metadata=OAuthClientMetadata(
                client_name="PR Intelligence Agent",
                redirect_uris=[f"http://localhost:{port}/callback"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
            ),
            storage=FileTokenStorage(),
            redirect_handler=_no_interactive,
            callback_handler=_no_callback,
        )

    async def _call(self, tool: str, args: dict, read_timeout: float = 60.0) -> dict:
        from datetime import timedelta

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url = get_settings().teams_mcp_url
        async with _TOKEN_LOCK, \
                streamablehttp_client(
                    url, auth=self._auth_provider(),
                    timeout=timedelta(seconds=read_timeout),
                    sse_read_timeout=timedelta(seconds=read_timeout),
                ) as (read, write, *_), \
                ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                tool, args, read_timeout_seconds=timedelta(seconds=read_timeout)
            )
            out = {}
            for item in result.content:
                text = getattr(item, "text", None)
                if text:
                    out.setdefault("text", "")
                    out["text"] += text
            return out

    async def send(self, address: dict, message: OutboundMessage) -> None:
        """address: {kind: teams_chat|teams_channel|email, chat_id|team_id/channel_id|to,
        message_id?}. Attachments upload to OneDrive first, then link in the message."""
        kind = address.get("kind", "teams_chat")
        text = message.text
        links = await self._upload_attachments(message.attachments)
        if links:
            text += "\n\nAttachments:\n" + "\n".join(f"- {n}: {u}" for n, u in links)

        if kind == "teams_channel":
            await self._call("channel_message_reply", {
                "team_id": address["team_id"], "channel_id": address["channel_id"],
                "message_id": address.get("message_id"), "content": text,
            })
        elif kind == "email":
            await self._call("mail_reply", {
                "message_id": address.get("message_id"), "comment": text,
            })
        else:
            await self._call("chat_message_send", {
                "chat_id": address["chat_id"], "content": text,
            })
        log.info("teams.sent", kind=kind)

    async def _upload_attachments(self, attachments) -> list[tuple[str, str]]:
        links: list[tuple[str, str]] = []
        for name, data, _mime in attachments:
            with contextlib.suppress(Exception):
                import base64

                res = await self._call("drive_item_upload", {
                    "name": name, "content_base64": base64.b64encode(data).decode(),
                })
                url = res.get("text", "")
                if url:
                    links.append((name, url))
        return links

    async def subscribe(self) -> bool:
        """Activate mention capture across chats, channels, and mail. Must run
        before get_pending_mentions returns anything."""
        try:
            # Render free tier: subscribe is slow; 60s default crashes it (needs ~180s)
            await self._call("subscribe_to_mentions",
                             {"resources": ["teams_chats", "teams_channels", "email"]},
                             read_timeout=180.0)
            log.info("teams.subscribed")
            return True
        except Exception as exc:
            log.info("teams.subscribe_failed", error=str(exc)[:150])
            return False

    async def renew(self) -> None:
        with contextlib.suppress(Exception):
            await self._call("renew_subscriptions", {})

    async def poll_inbound(self) -> AsyncIterator[ChannelInbound]:
        try:
            res = await self._call("get_pending_mentions", {})
        except Exception as exc:
            log.info("teams.poll_failed", error=str(exc)[:150])
            return
        import json

        with contextlib.suppress(Exception):
            mentions = json.loads(res.get("text", "[]"))
            for m in mentions if isinstance(mentions, list) else []:
                ch = "teams_channel" if m.get("channel_id") else "teams_chat"
                yield ChannelInbound(
                    channel=ch, sender=m.get("from", ""), text=m.get("text", ""),
                    thread_ref=m.get("id", ""), raw_id=m.get("id", ""),
                    address={
                        "kind": ch, "chat_id": m.get("chat_id"),
                        "team_id": m.get("team_id"), "channel_id": m.get("channel_id"),
                        "message_id": m.get("id"),
                    },
                )
                with contextlib.suppress(Exception):
                    await self._call("mark_mention_processed", {"id": m.get("id")})
