"""Stage 4 invariants: FOR-UPDATE edits log corrections, cascade to syndicated
copies, invalidate the charts cache, and keep approval flags independent."""

import uuid

import pytest
from sqlalchemy import select

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.db.base import get_sessionmaker
from app.db.models import CorrectionEvent, Project
from app.db.models import Session as SessionRow
from app.services import review_service


@pytest.fixture
async def seeded_session() -> tuple[str, str]:
    async with get_sessionmaker()() as db, db.begin():
        project = Project(name="rv", brand_name="Trane")
        db.add(project)
        await db.flush()
        row = SessionRow(
            project_id=project.id, status="tagged",
            tagged_file_key="", charts_data_file_key="sessions/x/charts.json",
        )
        db.add(row)
        await db.flush()
        pid, sid = str(project.id), str(row.id)
        row.tagged_file_key = keys.tagged_file(sid)

    await get_artifact_store().put_json(
        keys.tagged_file(sid),
        {"articles": [
            {"id": "A0", "title": "Original", "url": "https://a.com/1",
             "publisher_domain": "a.com", "content": "body",
             "xai_sentiment": "NEG", "xai_theme": "Launch", "xai_section": "Brand News",
             "sentiment_confidence": 0.6, "relevancy_confidence": 0.9,
             "is_approved": False, "is_approved_for_monitoring": False,
             "syndicated_urls": ["https://b.com/copy"]},
            {"id": "A1", "title": "Syndicated copy", "url": "https://b.com/copy",
             "publisher_domain": "b.com", "content": "body",
             "xai_sentiment": "NEG", "xai_theme": "Launch", "xai_section": "Brand News",
             "is_approved": False, "is_approved_for_monitoring": False},
        ]},
    )
    return pid, sid


async def test_edit_logs_correction_cascades_and_invalidates(seeded_session):
    pid, sid = seeded_session
    updated = await review_service.edit_tags(
        project_id=pid, session_id=sid, article_id="A0",
        changes={"xai_sentiment": "NEU"},
    )
    assert updated["xai_sentiment"] == "NEU"
    assert updated["sentiment_confidence"] == 1.0     # human-set confidence

    payload = await get_artifact_store().get_json(keys.tagged_file(sid))
    a1 = next(a for a in payload["articles"] if a["id"] == "A1")
    assert a1["xai_sentiment"] == "NEU"               # cascade to syndicated copy

    async with get_sessionmaker()() as db:
        row = await db.get(SessionRow, uuid.UUID(sid))
        assert row.charts_data_file_key is None       # cache invalidated
        events = (
            await db.execute(select(CorrectionEvent)
                             .where(CorrectionEvent.session_id == uuid.UUID(sid)))
        ).scalars().all()
    assert len(events) == 1
    assert (events[0].original_value, events[0].corrected_value) == ("NEG", "NEU")
    assert events[0].article_features["domain"] == "a.com"


async def test_approval_flags_are_independent(seeded_session):
    pid, sid = seeded_session
    a = await review_service.set_approval(
        project_id=pid, session_id=sid, article_id="A0", is_approved=True
    )
    assert a["is_approved"] is True
    assert a["is_approved_for_monitoring"] is False   # untouched

    a = await review_service.set_approval(
        project_id=pid, session_id=sid, article_id="A0", is_approved_for_monitoring=True
    )
    assert a["is_approved"] is True and a["is_approved_for_monitoring"] is True


async def test_immutable_fields_rejected_and_delete_logged(seeded_session):
    pid, sid = seeded_session
    with pytest.raises(review_service.ReviewError, match="immutable"):
        await review_service.edit_tags(
            project_id=pid, session_id=sid, article_id="A0", changes={"title": "hack"}
        )

    remaining = await review_service.delete_article(
        project_id=pid, session_id=sid, article_id="A1"
    )
    assert remaining == 1
    async with get_sessionmaker()() as db:
        kinds = (
            await db.execute(select(CorrectionEvent.event_type)
                             .where(CorrectionEvent.session_id == uuid.UUID(sid)))
        ).scalars().all()
    assert "delete" in kinds


async def test_add_article_assigns_next_id(seeded_session):
    pid, sid = seeded_session
    added = await review_service.add_article(
        project_id=pid, session_id=sid,
        article={"title": "Manual add", "url": "https://c.com/new"},
    )
    assert added["id"] == "A2"
    assert added["is_approved"] is False
