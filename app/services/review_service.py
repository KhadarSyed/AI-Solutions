"""Stage 4 — human review. Every mutation is one transaction:
SELECT … FOR UPDATE on the tagged_file artifact → mutate JSONB → correction
event → charts cache invalidation → embedding flag sync (post-commit)."""

import contextlib
import json
import uuid
from typing import Any

from sqlalchemy import select, update

from app.artifacts import keys
from app.db.base import get_sessionmaker
from app.db.models import Artifact
from app.db.models import Session as SessionRow
from app.memory import vector_store
from app.memory.correction_memory import log_correction
from app.observability.logging import get_logger

log = get_logger(__name__)

EDITABLE_FIELDS = {"xai_sentiment", "xai_theme", "xai_section", "priority_watch"}
IMMUTABLE_FIELDS = {"title", "content", "url", "publisher_name", "publisher_domain",
                    "published_date", "author"}


class ReviewError(Exception):
    pass


def _features(article: dict) -> dict:
    return {
        "domain": article.get("publisher_domain", ""),
        "length": len(article.get("content") or ""),
        "query_group": article.get("query_group", ""),
        "entities": (article.get("entities") or {}).get("brand_of_interest", []),
    }


async def _locked_mutation(session_id: str, mutate) -> tuple[dict, list[dict]]:
    """Run `mutate(payload) -> list[correction_dicts]` under FOR UPDATE; returns
    (new_payload, corrections). Cache invalidation happens in the same tx."""
    key = keys.tagged_file(session_id)
    async with get_sessionmaker()() as db, db.begin():
        row = (
            await db.execute(select(Artifact).where(Artifact.key == key).with_for_update())
        ).scalar_one_or_none()
        if row is None or row.json_data is None:
            raise ReviewError(f"no tagged articles for session {session_id}")

        payload: dict = json.loads(json.dumps(row.json_data))  # deep copy
        corrections = mutate(payload) or []

        raw = json.dumps(payload, ensure_ascii=False).encode()
        import hashlib

        await db.execute(
            update(Artifact).where(Artifact.id == row.id).values(
                json_data=payload, size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), version=Artifact.version + 1,
            )
        )
        # any mutation invalidates cached dashboards
        await db.execute(
            update(SessionRow).where(SessionRow.id == uuid.UUID(session_id)).values(
                charts_data_file_key=None, status="in_review",
                articles_count=len(payload.get("articles", [])),
            )
        )
    return payload, corrections


def _find(payload: dict, article_id: str) -> dict:
    for a in payload.get("articles", []):
        if a.get("id") == article_id:
            return a
    raise ReviewError(f"article {article_id} not found")


async def list_articles(session_id: str, offset: int = 0, limit: int = 50) -> dict:
    from app.artifacts.factory import get_artifact_store

    payload = await get_artifact_store().get_json(keys.tagged_file(session_id))
    articles = payload.get("articles", [])
    return {"total": len(articles), "articles": articles[offset:offset + limit]}


async def edit_tags(
    *, project_id: str, session_id: str, article_id: str, changes: dict[str, Any]
) -> dict:
    bad = set(changes) - EDITABLE_FIELDS
    if bad & IMMUTABLE_FIELDS:
        raise ReviewError(f"immutable fields: {sorted(bad & IMMUTABLE_FIELDS)}")
    if bad:
        raise ReviewError(f"not editable: {sorted(bad)} (allowed: {sorted(EDITABLE_FIELDS)})")

    def mutate(payload: dict) -> list[dict]:
        article = _find(payload, article_id)
        corrections = []
        targets = [article]
        # edits cascade to syndicated copies present in this session
        synd_urls = set(article.get("syndicated_urls", []))
        if synd_urls:
            targets += [a for a in payload["articles"]
                        if a.get("url") in synd_urls and a is not article]
        for field, new_value in changes.items():
            old = article.get(field)
            if old == new_value:
                continue
            conf_field = {
                "xai_sentiment": "sentiment_confidence",
                "xai_theme": "theme_confidence",
                "xai_section": "section_confidence",
            }.get(field)
            corrections.append({
                "field_name": field, "original_value": str(old),
                "corrected_value": str(new_value),
                "original_confidence": article.get(conf_field) if conf_field else None,
                "article_features": _features(article),
            })
            for t in targets:
                t[field] = new_value
                if conf_field:
                    t[conf_field] = 1.0  # human-set
        return corrections

    payload, corrections = await _locked_mutation(session_id, mutate)
    for c in corrections:
        await log_correction(project_id=project_id, session_id=session_id,
                             article_id=article_id, event_type="edit", **c)
    with contextlib.suppress(Exception):
        article = _find(payload, article_id)
        await vector_store.sync_flags(
            session_id, article_id,
            section=article.get("xai_section"), sentiment=article.get("xai_sentiment"),
        )
    return _find(payload, article_id)


async def set_approval(
    *, project_id: str, session_id: str, article_id: str,
    is_approved: bool | None = None, is_approved_for_monitoring: bool | None = None,
) -> dict:
    def mutate(payload: dict) -> list[dict]:
        article = _find(payload, article_id)
        changed = []
        if is_approved is not None and article.get("is_approved") != is_approved:
            article["is_approved"] = is_approved
            changed.append(("is_approved", is_approved))
        if (is_approved_for_monitoring is not None
                and article.get("is_approved_for_monitoring") != is_approved_for_monitoring):
            article["is_approved_for_monitoring"] = is_approved_for_monitoring
            changed.append(("is_approved_for_monitoring", is_approved_for_monitoring))
        return [{
            "field_name": f, "original_value": str(not v), "corrected_value": str(v),
            "original_confidence": None, "article_features": _features(article),
        } for f, v in changed]

    payload, corrections = await _locked_mutation(session_id, mutate)
    for c in corrections:
        await log_correction(project_id=project_id, session_id=session_id,
                             article_id=article_id, event_type="approve", **c)
    with contextlib.suppress(Exception):
        await vector_store.sync_flags(
            session_id, article_id,
            is_approved=is_approved, is_approved_for_monitoring=is_approved_for_monitoring,
        )
    return _find(payload, article_id)


async def delete_article(*, project_id: str, session_id: str, article_id: str) -> int:
    def mutate(payload: dict) -> list[dict]:
        article = _find(payload, article_id)
        payload["articles"] = [a for a in payload["articles"] if a.get("id") != article_id]
        return [{
            "field_name": "article", "original_value": article.get("title", ""),
            "corrected_value": None, "original_confidence":
                article.get("relevancy_confidence"),
            "article_features": _features(article),
        }]

    payload, corrections = await _locked_mutation(session_id, mutate)
    for c in corrections:
        await log_correction(project_id=project_id, session_id=session_id,
                             article_id=article_id, event_type="delete", **c)
    with contextlib.suppress(Exception):
        await vector_store.delete_article(session_id, article_id)
    return len(payload["articles"])


async def add_article(*, project_id: str, session_id: str, article: dict) -> dict:
    """Add a manually-provided (or fetched-and-tagged) article; id assigned here."""
    def mutate(payload: dict) -> list[dict]:
        existing = payload.get("articles", [])
        next_id = f"A{max((int(a['id'][1:]) for a in existing if str(a.get('id', '')).startswith('A')), default=-1) + 1}"
        article["id"] = next_id
        article.setdefault("is_approved", False)
        article.setdefault("is_approved_for_monitoring", False)
        existing.append(article)
        payload["articles"] = existing
        return [{
            "field_name": "article", "original_value": None,
            "corrected_value": article.get("title", ""),
            "original_confidence": None, "article_features": _features(article),
        }]

    payload, corrections = await _locked_mutation(session_id, mutate)
    for c in corrections:
        await log_correction(project_id=project_id, session_id=session_id,
                             article_id=payload["articles"][-1]["id"],
                             event_type="add", **c)
    return payload["articles"][-1]
