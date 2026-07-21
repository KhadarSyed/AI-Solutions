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

# Serialize all teams-mcp calls process-wide: refresh rotates the refresh token,
# and concurrent refreshes would invalidate each other. One shared token store.
_TOKEN_LOCK = asyncio.Lock()
_STORE = None


def _store():
    global _STORE
    if _STORE is None:
        from app.channels.teams_token_store import FileTokenStorage

        _STORE = FileTokenStorage()
    return _STORE


def _unwrap(exc: BaseException) -> str:
    """anyio TaskGroups wrap the real cause in an ExceptionGroup — surface it."""
    subs = getattr(exc, "exceptions", None)
    if subs:
        return " | ".join(_unwrap(e) for e in subs)
    return f"{type(exc).__name__}: {exc}"


def _clean_text(text: str) -> str:
    """Strip the corporate 'EXTERNAL EMAIL … Secured by Check Point' banner that
    email gateways prepend, so intent/brand detection sees the actual message."""
    import re

    stripped = re.sub(r"(?is)^.*?secured by check point\b[\s:.-]*", "", text, count=1)
    return stripped.strip() or text.strip()


class TeamsMcpAdapter(ChannelAdapter):
    channel = "teams"

    def enabled(self) -> bool:
        # requires both the URL and a persisted durable token (run scripts/teams_auth.py)
        if not get_settings().teams_mcp_url:
            return False
        from app.channels.teams_token_store import FileTokenStorage

        return FileTokenStorage().has_credentials()

    def _auth(self):
        """A minimal bearer auth that injects the current access token. We keep
        the token fresh out-of-band (see _ensure_fresh); no SDK OAuth flow, so
        there is no interactive fallback to die on."""
        import httpx

        store = _store()

        class _Bearer(httpx.Auth):
            def auth_flow(self, request):
                tok = store.access_token()
                if tok:
                    request.headers["Authorization"] = f"Bearer {tok}"
                yield request

        return _Bearer()

    async def refresh_token(self, force: bool = False) -> bool:
        async with _TOKEN_LOCK:
            return await _store().refresh(force=force)

    async def _call(self, tool: str, args: dict, read_timeout: float = 60.0) -> dict:
        from datetime import timedelta

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        # deterministic, serialized refresh right before use (no-op if still valid)
        async with _TOKEN_LOCK:
            await _store().refresh(force=False)

        url = get_settings().teams_mcp_url
        async with streamablehttp_client(
                    url, auth=self._auth(),
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

    @staticmethod
    def _msg_id(out: dict) -> str | None:
        """Best-effort extraction of the sent/replied message id from the MCP response,
        so the notifier can anchor the whole task to one email thread."""
        import json
        import re

        text = (out or {}).get("text", "") or ""
        with contextlib.suppress(Exception):
            data = json.loads(text)
            stack = [data]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    for k, v in cur.items():
                        if k in ("id", "messageId", "sentMessageId") and isinstance(v, str):
                            return v
                        stack.append(v)
                elif isinstance(cur, list):
                    stack.extend(cur)
        m = re.search(r"AAMk[A-Za-z0-9_\-/+]{20,}={0,2}", text)  # Graph message-id shape
        return m.group(0) if m else None

    async def send(self, address: dict, message: OutboundMessage) -> str | None:
        """address: {kind: teams_chat|teams_channel|email, chat_id|team_id/channel_id|to,
        message_id?}. Returns the sent message id when available (email), so the caller can
        thread the task. Teams attachments upload to OneDrive then link; email attachments
        go inline via Graph mail_send."""
        kind = address.get("kind", "teams_chat")

        if kind == "email":
            return await self._send_mail(address, message)

        text = message.text
        links = await self._upload_attachments(message.attachments)
        if links:
            text += "\n\nAttachments:\n" + "\n".join(f"- {n}: {u}" for n, u in links)

        if kind == "teams_channel":
            await self._call("channel_message_reply", {
                "team_id": address["team_id"], "channel_id": address["channel_id"],
                "message_id": address.get("message_id"), "content": text,
            })
        else:
            await self._call("chat_message_send", {
                "chat_id": address["chat_id"], "content": text,
            })
        log.info("teams.sent", kind=kind)

    # The teams-mcp /mcp POST body limit is low (~100 KB): a single ~90 KB CSV
    # sends, but two files (154 KB) 413. So anything but a tiny file is uploaded
    # to OneDrive and attached by reference (attachItems), keeping the POST small.
    INLINE_MAX = 40_000

    async def _send_mail(self, address: dict, message: OutboundMessage) -> None:
        """Send from the agent's real mailbox via Graph. When we have the
        initiator's original message id we reply IN-THREAD (mail_reply) so the
        whole exchange — collected CSV, tagged CSV, dashboard — stays in one
        thread; only a cold, thread-less send uses mail_send. Large files go to
        OneDrive and attach by reference so the message never 413s."""
        import base64

        inline: list[dict] = []
        attach_items: list[dict] = []
        for name, data, mime in message.attachments:
            if len(data) <= self.INLINE_MAX:
                inline.append({"name": name,
                               "contentType": mime or "application/octet-stream",
                               "contentBytesBase64": base64.b64encode(data).decode()})
                continue
            item = await self._upload_to_drive(name, data)
            if item:
                attach_items.append(item)
            else:  # upload failed — inline as a last resort (may 413, but don't drop)
                inline.append({"name": name,
                               "contentType": mime or "application/octet-stream",
                               "contentBytesBase64": base64.b64encode(data).decode()})

        cc = message.cc or address.get("cc") or []
        msg_id = address.get("message_id")
        if msg_id:
            # reply comment renders as HTML in-thread when we have a styled body
            args: dict = {"messageId": msg_id, "comment": message.html or message.text}
        else:
            args = {"to": address.get("to"),
                    "subject": message.subject or "PR Intelligence Agent",
                    "body": message.html or message.text,
                    "contentType": "HTML" if message.html else "Text"}
        if cc:
            args["cc"] = cc
        if inline:
            args["attachments"] = inline
        if attach_items:
            args["attachItems"] = attach_items

        tool = "mail_reply" if msg_id else "mail_send"
        out = await self._call(tool, args, read_timeout=180.0)
        sent_id = self._msg_id(out)
        log.info("teams.sent", kind="email", mode="reply" if msg_id else "send",
                 inline=len(inline), drive=len(attach_items), sent_id=bool(sent_id))
        # for a reply keep threading on the original anchor; for a fresh send return the new id
        return msg_id or sent_id

    async def _upload_to_drive(self, name: str, data: bytes) -> dict | None:
        """Upload bytes to the agent's OneDrive via a Graph upload session (no
        base64 through the size-limited MCP POST) and return an attachItems entry
        {itemId, driveId, name}."""
        import json as _json

        try:
            res = await self._call("drive_request_upload", {"name": name},
                                    read_timeout=90.0)
            info = _json.loads(res.get("text", "{}"))
            upload_url = info.get("uploadUrl")
            if not upload_url:
                return None

            import httpx

            size = len(data)
            async with httpx.AsyncClient(timeout=180) as client:
                put = await client.put(
                    upload_url, content=data,
                    headers={"Content-Range": f"bytes 0-{size - 1}/{size}"},
                )
            put.raise_for_status()
            item = put.json()
            item_id = item.get("id")
            drive_id = info.get("driveId") or (item.get("parentReference") or {}).get("driveId")
            if item_id:
                log.info("teams.drive_uploaded", name=name, bytes=size)
                return {"itemId": item_id, "driveId": drive_id, "name": name}
        except Exception as exc:
            log.warning("teams.drive_upload_failed", name=name, error=_unwrap(exc)[:200])
        return None

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
        before get_pending_mentions returns anything. NOTE: the tool parameter is
        `sources` (not `resources`) and `match` sets the trigger keywords."""
        keywords = [k.strip() for k in get_settings().mention_keywords.split(",")
                    if k.strip()]
        try:
            # Render free tier: subscribe is slow; 60s default crashes it (needs ~180s)
            await self._call("subscribe_to_mentions",
                             {"sources": ["teams_chats", "teams_channels", "email"],
                              "match": {"keywords": keywords}},
                             read_timeout=180.0)
            log.info("teams.subscribed", keywords=len(keywords))
            return True
        except Exception as exc:
            log.info("teams.subscribe_failed", error=_unwrap(exc)[:300])
            return False

    async def bootstrap(self, resubscribe: bool = True) -> bool:
        """Startup: mint a fresh access token, prove it works (identity check),
        and (re)activate the mention subscription. Called at agent boot so
        triggering by email or Teams works from the first tick, not lazily."""
        if not await self.refresh_token(force=True):
            log.warning("teams.bootstrap_no_token")
            return False
        try:
            me = await self._call("me_get", {}, read_timeout=60.0)
            log.info("teams.identity_ok", who=str(me.get("text", ""))[:200])
        except Exception as exc:
            log.warning("teams.identity_failed", error=_unwrap(exc)[:300])
            return False
        if not resubscribe:
            return True
        return await self.subscribe()

    async def renew(self) -> None:
        # keep the access token alive (well before the 1 h expiry) and the
        # Graph subscription active
        with contextlib.suppress(Exception):
            await self.refresh_token(force=True)
        with contextlib.suppress(Exception):
            await self._call("renew_subscriptions", {})

    async def poll_inbound(self) -> AsyncIterator[ChannelInbound]:
        try:
            res = await self._call("get_pending_mentions", {})
        except Exception as exc:
            log.info("teams.poll_failed", error=_unwrap(exc)[:300])
            return
        import json

        raw = res.get("text", "[]")
        try:
            data = json.loads(raw)
        except Exception:
            return
        # server returns a bare list OR {"mentions": [...], "note": ...}
        mentions = data.get("mentions", []) if isinstance(data, dict) else data
        if not isinstance(mentions, list):
            mentions = []
        if mentions:
            log.info("teams.poll", mentions=len(mentions), sample=str(raw)[:300])
        for m in mentions:
            inbound = self._to_inbound(m)
            if inbound is not None:
                yield inbound
            with contextlib.suppress(Exception):
                # tool parameter is `mention_id` (not `id`)
                await self._call("mark_mention_processed", {"mention_id": m.get("id")})

    @staticmethod
    def _to_inbound(m: dict) -> ChannelInbound | None:
        """Map a teams-mcp mention (real schema: source_type / raw_text / task_text
        / sender_email / message_id) onto a ChannelInbound. Email, chat, and
        channel mentions have different shapes."""
        mid = m.get("id") or m.get("message_id") or ""
        text = _clean_text(m.get("raw_text") or m.get("task_text") or m.get("text", ""))
        src = m.get("source_type") or (
            "teams_channel" if m.get("channel_id") else "teams_chat")

        if src == "email":
            sender = m.get("sender_email") or m.get("from", "")
            return ChannelInbound(
                channel="email", sender=sender, text=text,
                subject=m.get("subject", ""), thread_ref=mid, raw_id=mid,
                address={"kind": "email", "to": sender,
                         "message_id": m.get("message_id") or mid},
            )
        if src == "teams_channel":
            return ChannelInbound(
                channel="teams_channel", sender=m.get("sender_email") or m.get("from", ""),
                text=text, thread_ref=mid, raw_id=mid,
                address={"kind": "teams_channel", "team_id": m.get("team_id"),
                         "channel_id": m.get("channel_id"),
                         "message_id": m.get("message_id") or mid},
            )
        return ChannelInbound(
            channel="teams_chat", sender=m.get("sender_email") or m.get("from", ""),
            text=text, thread_ref=mid, raw_id=mid,
            address={"kind": "teams_chat", "chat_id": m.get("chat_id"),
                     "message_id": m.get("message_id") or mid},
        )
