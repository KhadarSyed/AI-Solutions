"""Lexical recall — BM25-lite over the tagged_file JSONB, no embeddings.

The embedding-independent fallback for `retrieve()`: when the Azure embedding deployment
is unavailable (or the article_embeddings table is empty), recall real approved articles
straight from the source-of-truth artifact and score them by keyword relevance. The
candidates are still reranked by the FlashRank cross-encoder downstream, and a relevance
floor drops anything the reranker scores as irrelevant — so this changes only HOW articles
are found, never the grounding discipline."""

import math
import re
import uuid
from datetime import date

from sqlalchemy import select

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.db.base import get_sessionmaker
from app.db.models import Session as SessionRow
from app.observability.logging import get_logger
from app.retrieval.bi_encoder import RecallHit

log = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = (
    "a an the and or of to in on for with at by from as is are was were be been being this "
    "that these those it its their his her our your they we you i he she them us about into "
    "over under out up down off no not can will would should could may might do does did has "
    "have had than then so such but if while when what which who whom whose how why"
)
_STOP = frozenset(_STOP_WORDS.split())
_K1 = 1.5
_B = 0.75
_TITLE_BOOST = 3  # title terms count this many times toward term frequency


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if len(t) > 1 and t not in _STOP]


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def _articles_for(project_id, session_id) -> list[tuple[str, dict]]:
    """(session_id, article) pairs from tagged_file(s). One session when given, else every
    tagged session of the project."""
    store = get_artifact_store()
    sids: list[str] = []
    if session_id:
        sids = [str(session_id)]
    else:
        async with get_sessionmaker()() as db:
            rows = (
                await db.execute(
                    select(SessionRow.id).where(
                        SessionRow.project_id == uuid.UUID(str(project_id)),
                        SessionRow.tagged_file_key.isnot(None),
                    )
                )
            ).all()
        sids = [str(r[0]) for r in rows]

    pairs: list[tuple[str, dict]] = []
    for sid in sids:
        try:
            payload = await store.get_json(keys.tagged_file(sid))
        except Exception:
            continue
        for a in payload.get("articles", []):
            pairs.append((sid, a))
    return pairs


def _keep(
    a: dict,
    *,
    approved_only: bool,
    monitoring_audience: bool,
    section: str | None,
    sentiment: str | None,
    date_from: date | None,
    date_to: date | None,
) -> bool:
    if approved_only:
        flag = "is_approved_for_monitoring" if monitoring_audience else "is_approved"
        if not a.get(flag):
            return False
    if section and a.get("xai_section") != section:
        return False
    if sentiment and a.get("xai_sentiment") != sentiment:
        return False
    if date_from or date_to:
        d = _parse_date(a.get("published_date"))
        if d is None:
            return False
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False
    return True


async def recall_topk_lexical(
    *,
    project_id: uuid.UUID | str,
    query: str,
    k: int = 50,
    approved_only: bool = True,
    session_id: uuid.UUID | str | None = None,
    section: str | None = None,
    sentiment: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    monitoring_audience: bool = False,
) -> list[RecallHit]:
    q_terms = _tokens(query)
    if not q_terms:
        return []

    pairs = [
        (sid, a)
        for sid, a in await _articles_for(project_id, session_id)
        if _keep(
            a,
            approved_only=approved_only,
            monitoring_audience=monitoring_audience,
            section=section,
            sentiment=sentiment,
            date_from=date_from,
            date_to=date_to,
        )
    ]
    if not pairs:
        return []

    # Tokenize once; title terms weighted by repetition.
    docs: list[list[str]] = []
    for _, a in pairs:
        toks = _tokens(a.get("title", "")) * _TITLE_BOOST + _tokens(a.get("content", ""))
        docs.append(toks)

    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n
    q_set = set(q_terms)
    df = {t: 0 for t in q_set}
    for d in docs:
        seen = set(d) & q_set
        for t in seen:
            df[t] += 1
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q_set}

    scored: list[tuple[float, str, dict]] = []
    for (sid, a), d in zip(pairs, docs, strict=True):
        if not d:
            continue
        dl = len(d)
        tf: dict[str, int] = {}
        for t in d:
            if t in q_set:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            continue
        s = 0.0
        for t, f in tf.items():
            s += idf[t] * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / avgdl))
        scored.append((s, sid, a))

    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]
    max_s = top[0][0] or 1.0

    hits: list[RecallHit] = []
    for s, sid, a in top:
        title = a.get("title", "")
        hits.append(
            RecallHit(
                article_id=a.get("id", ""),
                session_id=sid,
                content_preview=f"{title} — {(a.get('content') or '')[:800]}",
                section=a.get("xai_section"),
                sentiment=a.get("xai_sentiment"),
                published_at=_parse_date(a.get("published_date")),
                similarity=round(s / max_s, 4),
                meta={
                    "publisher": a.get("publisher_name", ""),
                    "domain": a.get("publisher_domain", ""),
                    "url": a.get("url", ""),
                    "theme": a.get("xai_theme", ""),
                    "title": title,
                },
            )
        )
    log.info("lexical.recall", candidates=len(pairs), scored=len(scored), returned=len(hits))
    return hits
