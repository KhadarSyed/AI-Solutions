"""Autonomy + reliability unit tests.

Covers the self-configuring / hands-off behaviors added for production:
brand extraction for known and arbitrary brands, the auto-provision domain gate,
competitor overrides, id-anchored threading extraction, and gate-escalation state —
all pure/deterministic (no network, LLM, or DB).
"""
from types import SimpleNamespace

from app.agents.email_agent import (
    _authorized_domain,
    _extract_brand_generic,
    _parse_competitor_override,
    subject_allowed,
)
from app.channels.teams_mcp import TeamsMcpAdapter


class TestBrandExtraction:
    def test_known_brands_canonical(self):
        assert _extract_brand_generic("Monitor Trane", "") == "Trane"
        assert _extract_brand_generic("Monitor BeOne", "") == "BeOne"
        assert _extract_brand_generic("Monitor Otsuka", "") == "Otsuka"

    def test_case_insensitive_known(self):
        assert _extract_brand_generic("monitor TRANE now", "") == "Trane"

    def test_random_brands(self):
        assert _extract_brand_generic("Start monitoring Pfizer for the last 2 days", "") == "Pfizer"
        assert _extract_brand_generic("Track Nike coverage", "") == "Nike"
        assert _extract_brand_generic("Monitor Samsung news", "") == "Samsung"

    def test_filler_trimmed(self):
        # trailing 'coverage'/'for ...'/'news' must not become part of the brand
        assert _extract_brand_generic("Monitor Tesla for this week", "") == "Tesla"

    def test_body_fallback(self):
        assert _extract_brand_generic("Re: hello", "please monitor Adidas") == "Adidas"

    def test_no_trigger_is_empty(self):
        assert _extract_brand_generic("random subject", "") == ""
        assert _extract_brand_generic("", "") == ""
        assert _extract_brand_generic("Your invoice is due", "") == ""


class TestAuthorizedDomain:
    def _patch(self, monkeypatch, allow: str):
        monkeypatch.setattr(
            "app.config.settings.get_settings",
            lambda: SimpleNamespace(authorized_sender_domains=allow))

    def test_disabled_by_default(self, monkeypatch):
        self._patch(monkeypatch, "")          # empty allowlist → nobody auto-provisions
        assert not _authorized_domain("khadar.syed@infovision.com")

    def test_allowlisted_domain(self, monkeypatch):
        self._patch(monkeypatch, "infovision.com")
        assert _authorized_domain("khadar.syed@infovision.com")
        assert _authorized_domain("someone.else@infovision.com")

    def test_outside_allowlist_rejected(self, monkeypatch):
        self._patch(monkeypatch, "infovision.com")
        assert not _authorized_domain("attacker@evil.com")

    def test_multiple_domains(self, monkeypatch):
        self._patch(monkeypatch, "infovision.com, partner.io")
        assert _authorized_domain("a@partner.io")
        assert not _authorized_domain("a@other.com")


class TestCompetitorOverride:
    def test_explicit_list(self):
        assert _parse_competitor_override("competitors: Carrier, Daikin, Lennox") == [
            "Carrier", "Daikin", "Lennox"]

    def test_dash_separator(self):
        assert _parse_competitor_override("competitor - Roche; Novartis") == ["Roche", "Novartis"]

    def test_none_when_absent(self):
        assert _parse_competitor_override("please monitor coverage") is None


class TestThreadingIdExtraction:
    def test_flat_id(self):
        import json
        out = {"text": json.dumps({"id": "AAMkAGImsgid0000000000000000=="})}
        assert TeamsMcpAdapter._msg_id(out) == "AAMkAGImsgid0000000000000000=="

    def test_nested_message_id(self):
        import json
        out = {"text": json.dumps({"message": {"id": "AAMkAGInested11111111111111=="}})}
        assert TeamsMcpAdapter._msg_id(out) == "AAMkAGInested11111111111111=="

    def test_sent_message_id_key(self):
        import json
        out = {"text": json.dumps({"ok": True, "sentMessageId": "AAMkAGIsent2222222222222222=="})}
        assert TeamsMcpAdapter._msg_id(out) == "AAMkAGIsent2222222222222222=="

    def test_none_when_absent(self):
        assert TeamsMcpAdapter._msg_id({"text": "no id here"}) is None
        assert TeamsMcpAdapter._msg_id({}) is None


class TestSubjectGating:
    def test_all_three_brands(self):
        brands = ["BeOne", "Trane", "Otsuka"]
        assert subject_allowed("Monitor BeOne", brands)
        assert subject_allowed("Monitor Trane", brands)
        assert subject_allowed("Monitor Otsuka", brands)

    def test_gate_reply_allowed(self):
        assert subject_allowed("Re: [OTSUKA-20260722-001] approve", ["Otsuka"])

    def test_unrelated_rejected(self):
        assert not subject_allowed("Lunch?", ["BeOne", "Trane", "Otsuka"])


class TestEscalationState:
    def test_state_from_run(self):
        from app.orchestration.gate_escalation import _state_from_run
        run = SimpleNamespace(
            id="run-123", origin_channel="email",
            origin_address={"task_id": "TRANE-20260722-001", "to": "k@x.com",
                            "message_id": "AAMkANCHOR=="})
        st = _state_from_run(run, "Trane")
        assert st["run_id"] == "run-123"
        assert st["task_id"] == "TRANE-20260722-001"
        assert st["origin_channel"] == "email"
        assert st["origin_address"]["message_id"] == "AAMkANCHOR=="
        assert st["brand"] == "Trane"
