"""Email channel — SMTP send + IMAP poll (greenmail locally). Message-ID dedupe."""

import contextlib
import email
from collections.abc import AsyncIterator
from email.message import EmailMessage

import aioimaplib
import aiosmtplib

from app.channels.base import ChannelAdapter, ChannelInbound, OutboundMessage
from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


class EmailAdapter(ChannelAdapter):
    channel = "email"

    def enabled(self) -> bool:
        s = get_settings()
        return bool(s.smtp_host and s.imap_host)

    async def send(self, address: dict, message: OutboundMessage) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["From"] = s.agent_email
        msg["To"] = address.get("to", "")
        msg["Subject"] = message.subject or "PR Intelligence Agent"
        if address.get("in_reply_to"):
            msg["In-Reply-To"] = address["in_reply_to"]
            msg["References"] = address["in_reply_to"]
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")
        for name, data, mime in message.attachments:
            maintype, _, subtype = mime.partition("/")
            msg.add_attachment(data, maintype=maintype or "application",
                               subtype=subtype or "octet-stream", filename=name)
        await aiosmtplib.send(msg, hostname=s.smtp_host, port=s.smtp_port)
        log.info("email.sent", to=address.get("to"), subject=msg["Subject"])

    async def poll_inbound(self) -> AsyncIterator[ChannelInbound]:
        s = get_settings()
        client = aioimaplib.IMAP4(host=s.imap_host, port=s.imap_port)
        try:
            await client.wait_hello_from_server()
            await client.login(s.agent_email, s.agent_email.split("@")[0])
            await client.select("INBOX")
            typ, data = await client.search("UNSEEN")
            if typ != "OK":
                return
            ids = (data[0].split() if data and data[0] else [])
            for raw_id in ids:
                num = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
                typ, msg_data = await client.fetch(num, "(RFC822)")
                if typ != "OK":
                    continue
                raw = next((p for p in msg_data if isinstance(p, bytearray | bytes)
                            and b"From:" in p), None)
                if not raw:
                    continue
                parsed = email.message_from_bytes(bytes(raw))
                yield ChannelInbound(
                    channel="email",
                    sender=email.utils.parseaddr(parsed.get("From", ""))[1],
                    subject=parsed.get("Subject", ""),
                    text=_plain_body(parsed),
                    thread_ref=parsed.get("Message-ID", ""),
                    raw_id=parsed.get("Message-ID", num),
                    address={"to": email.utils.parseaddr(parsed.get("From", ""))[1],
                             "in_reply_to": parsed.get("Message-ID", "")},
                    attachments=_attachments(parsed),
                )
        except Exception as exc:
            log.info("email.poll_failed", error=str(exc)[:150])
        finally:
            with contextlib.suppress(Exception):
                await client.logout()


def _attachments(msg: EmailMessage) -> list[tuple[str, bytes, str]]:
    """Extract file attachments (name, bytes, mime) — e.g. the edited monitoring CSV."""
    if not msg.is_multipart():
        return []
    out: list[tuple[str, bytes, str]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if part.get_content_disposition() == "attachment" or filename:
            data = part.get_payload(decode=True)
            if data:
                out.append((filename or "attachment", data, part.get_content_type()))
    return out


def _plain_body(msg: EmailMessage) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(errors="replace") if payload else str(msg.get_payload())
