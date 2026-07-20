import pytest

from app.services.vercel_publisher import project_name, publish_dashboard


def test_project_name_slug():
    assert project_name("BeOne", "BEONE-20260721-001") == "prsol-beone-beone-20260721-001"
    assert project_name("Johnson & Johnson", "X 1") == "prsol-johnson-johnson-x-1"


@pytest.mark.asyncio
async def test_publish_noop_without_token(monkeypatch):
    monkeypatch.setattr("app.services.vercel_publisher.get_settings",
                        lambda: type("S", (), {"vercel_token": ""})())
    assert await publish_dashboard("BeOne", "T-1", b"<html></html>") is None
