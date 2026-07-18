from app.agents.email_agent import _APPROVE_RE, _CHANGES_RE, InboundIntent
from app.channels.base import ChannelInbound, OutboundMessage
from app.channels.email_imap import EmailAdapter
from app.channels.teams_mcp import TeamsMcpAdapter


def test_gate_reply_regexes():
    assert _APPROVE_RE.search("Approved, go ahead")
    assert not _CHANGES_RE.search("Approved, go ahead")
    assert _CHANGES_RE.search("please add competitor Carrier")


def test_adapters_gate_on_config(monkeypatch):
    from app.config import settings as sm

    s = sm.get_settings()
    monkeypatch.setattr(s, "smtp_host", "localhost")
    monkeypatch.setattr(s, "imap_host", "localhost")
    assert EmailAdapter().enabled()
    monkeypatch.setattr(s, "teams_mcp_url", "")
    assert not TeamsMcpAdapter().enabled()
    monkeypatch.setattr(s, "teams_mcp_url", "https://x/mcp")
    assert TeamsMcpAdapter().enabled()


def test_channel_dataclasses():
    inbound = ChannelInbound(channel="email", sender="a@b.com", text="hi",
                             raw_id="<m1@b.com>")
    assert inbound.raw_id == "<m1@b.com>"
    msg = OutboundMessage(text="body", subject="s",
                          attachments=[("f.csv", b"a,b\n1,2\n", "text/csv")])
    assert msg.attachments[0][0] == "f.csv"


def test_inbound_intent_model():
    i = InboundIntent(kind="gate_decision", gate_decision="approved")
    assert i.kind == "gate_decision" and i.gate_decision == "approved"
