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


def test_resume_token_binds_to_run_and_resists_spoof():
    from app.security.auth import resume_token, token_in_text

    rid = "3d864ea4-9bd3-4d5e-8c9e-c22f85f4c818"
    token = resume_token(rid)
    assert token.startswith("RT-") and len(token) == 19
    # a reply quoting the original notification carries the token
    assert token_in_text(rid, f"Approved. {token}")
    # a forged token or one from another run does not verify
    assert not token_in_text(rid, "Approved. RT-0000000000000000")
    assert not token_in_text(rid, "Approved, go ahead")
    assert not token_in_text("different-run-id", f"Approved. {token}")
