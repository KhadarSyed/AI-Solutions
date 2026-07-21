"""ChannelAdapter — one interface for web, email, and Teams.

Each adapter can deliver a notification (gate CSV, report, answer) to its
channel and, if it listens, surface inbound messages as ChannelInbound for the
inbound router to map to a pending gate or a new query."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class OutboundMessage:
    text: str
    subject: str = ""
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)  # name, data, mime


@dataclass
class ChannelInbound:
    channel: str                       # email | teams_chat | teams_channel
    sender: str                        # email address / user id
    text: str
    subject: str = ""
    thread_ref: str = ""               # message-id / conversation id / mention id
    address: dict = field(default_factory=dict)   # reply-addressing for this thread
    raw_id: str = ""                   # dedupe key (Message-ID / mention id)
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)  # name, data, mime


class ChannelAdapter(ABC):
    channel: str = "base"

    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    async def send(self, address: dict, message: OutboundMessage) -> None:
        """Deliver to a specific conversation/thread/recipient."""

    async def poll_inbound(self) -> AsyncIterator[ChannelInbound]:
        """Yield new inbound messages. Adapters that don't listen yield nothing."""
        return
        yield  # pragma: no cover — makes this an async generator
