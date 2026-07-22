"""Stage 2 — collection: fleet fan-out → merge/dedupe → relevancy filter →
source_file artifact. Deterministic except the optional embedding relevancy cut."""

import hashlib
import re
import uuid

from sqlalchemy import update

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Session as SessionRow
from app.observability.logging import get_logger
from app.tools.connectors.base import Connector, RawArticle, SearchFilters
from app.tools.connectors.ddg import DuckDuckGoConnector
from app.tools.connectors.google_news_rss import GoogleNewsRSSConnector
from app.tools.connectors.keyed import (
    ApifyConnector,
    SerpApiConnector,
    TavilyConnector,
)
from app.tools.connectors.searxng import SearxngConnector
from app.tools.connectors.xpoz import XPozConnector

log = get_logger(__name__)

ALL_CONNECTORS: list[Connector] = [
    GoogleNewsRSSConnector(),
    SearxngConnector(),
    DuckDuckGoConnector(),
    SerpApiConnector(),
    TavilyConnector(),
    ApifyConnector(),
    XPozConnector(),
]


def enabled_connectors() -> list[Connector]:
    return [c for c in ALL_CONNECTORS if c.enabled()]


def _norm_url(url: str) -> str:
    url = url.strip().lower()
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/")


def _fingerprint(article: RawArticle) -> str:
    basis = re.sub(r"\W+", "", article.title.lower())[:120]
    return hashlib.sha1(basis.encode()).hexdigest()


def dedupe(articles: list[RawArticle]) -> tuple[list[RawArticle], dict[str, list[str]]]:
    """URL + title-fingerprint dedupe. Returns unique articles and a syndication
    map fingerprint → duplicate urls (used later to link syndicated copies)."""
    seen_urls: set[str] = set()
    by_fp: dict[str, RawArticle] = {}
    syndication: dict[str, list[str]] = {}

    for a in articles:
        nu = _norm_url(a.url)
        if not nu or nu in seen_urls:
            continue
        seen_urls.add(nu)
        fp = _fingerprint(a)
        if fp in by_fp:
            syndication.setdefault(fp, []).append(a.url)
            keeper = by_fp[fp]
            keeper_gnews = keeper.publisher_domain == "news.google.com"
            a_gnews = a.publisher_domain == "news.google.com"
            # prefer a real outlet over a news.google.com redirect; otherwise richest copy
            prefer_a = ((keeper_gnews and not a_gnews)
                        or (keeper_gnews == a_gnews and len(a.content) > len(keeper.content)))
            if prefer_a:
                syndication[fp].append(keeper.url)
                syndication[fp].remove(a.url)
                by_fp[fp] = a
            continue
        by_fp[fp] = a

    return list(by_fp.values()), syndication


def _cos(u: list[float], v: list[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v, strict=True))
    nu = sum(x * x for x in u) ** 0.5
    nv = sum(x * x for x in v) ** 0.5
    return dot / (nu * nv) if nu and nv else 0.0


def _within_window(articles: list[RawArticle], days_back: int) -> list[RawArticle]:
    """Hard date cut: drop anything published before (today − days_back). Source-side date
    filters are best-effort (SearXNG/DDG return older items), so this is the guarantee that a
    '2 day' window never surfaces 2024 coverage. Undated items are kept (can't be disproved)
    but counted so leakage is visible."""
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=max(1, days_back))
    kept: list[RawArticle] = []
    dropped = undated = 0
    for a in articles:
        d = a.published_date
        if d is None:
            undated += 1
            kept.append(a)
        elif d >= cutoff:
            kept.append(a)
        else:
            dropped += 1
    log.info("ingestion.date_window", days_back=days_back, cutoff=str(cutoff),
             before=len(articles), after=len(kept), dropped_old=dropped, undated=undated)
    return kept


async def relevancy_filter(
    articles: list[RawArticle], brand: str, queries: list[str],
    threshold: float | None = None,
) -> list[RawArticle]:
    """Drop off-topic coverage with an embedding-cosine cut. Each article is scored against
    a company-framed anchor for the SUBJECT it was collected for (brand or the specific
    competitor) — never a mixed centroid — so 'Amway Stadium' football doesn't ride in on
    the presence of 'Amway' in the anchor, and an NHL/celebrity 'BeOne' hit is dropped from
    the brand's coverage. Runs whenever embeddings are available (any backend); a hard
    embedding failure leaves collection intact (all kept)."""
    from app.llm_gateway.embeddings import embed, embeddings_available

    if not articles or not await embeddings_available():
        return articles
    if threshold is None:
        threshold = get_settings().relevancy_threshold

    def _anchor_text(subject: str) -> str:
        s = (subject or brand or "").strip()
        return f"{s} — the company/organization: business, product, research and industry news"

    try:
        subjects = sorted({(a.subject_brand or brand or "").strip() for a in articles})
        anchor_vecs = dict(zip(subjects, await embed([_anchor_text(s) for s in subjects]),
                               strict=True))
        texts = [f"{a.title}. {a.content[:400]}" for a in articles]
        vectors = await embed(texts)
    except Exception as exc:
        log.warning("ingestion.relevancy_skipped", error=str(exc)[:160])
        return articles

    kept = [a for a, vec in zip(articles, vectors, strict=True)
            if _cos(anchor_vecs[(a.subject_brand or brand or "").strip()], vec) >= threshold]
    log.info("ingestion.relevancy", before=len(articles), after=len(kept), threshold=threshold)
    return kept


def _subject_for(group: dict, article: RawArticle, brand: str) -> str:
    if group.get("subject_per_query"):
        return article.original_query or ""
    subj = group.get("subject")
    return brand if subj is None else subj


async def _persist_source_file(session_id: str, payload: dict) -> None:
    key = keys.source_file(session_id)
    await get_artifact_store().put_json(key, payload)
    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            update(SessionRow).where(SessionRow.id == uuid.UUID(session_id))
            .values(source_file_key=key, articles_count=payload["stats"]["kept"],
                    status="ingested")
        )


async def collect(
    *,
    session_id: str,
    brand: str,
    query_groups: list[dict],
    days_back: int | None = None,
    language: str = "en",
    country: str | None = None,
    max_per_query: int = 50,
    extra_articles: list[RawArticle] | None = None,
    run_id: str | None = None,
) -> dict:
    """Concurrent Sources Orchestrator: fan out (connector × query-group) under a
    semaphore, isolate per-source failures, stamp subject_brand, emit per-source
    status events, then dedupe → relevancy → persist source_file."""
    import asyncio
    import time

    from app.orchestration.events import get_event_bus

    if days_back is None:
        days_back = get_settings().collection_days_back   # default 48h
    filters = SearchFilters(
        days_back=days_back, language=language, country=country, max_results=max_per_query
    )
    sem = asyncio.Semaphore(get_settings().source_concurrency)
    bus = get_event_bus() if run_id else None

    collected: list[RawArticle] = list(extra_articles or [])
    errors: list[str] = []
    per_source: dict[str, int] = {"file_upload": len(collected)} if collected else {}

    async def run_one(connector: Connector, group: dict) -> None:
        queries = group.get("queries", [])
        if not queries:
            return
        if bus:
            await bus.emit(run_id, "source_started", node=connector.name,
                           payload={"group": group["name"], "queries": len(queries)})
        started = time.monotonic()
        async with sem:
            try:
                res = await connector.search(queries, filters)
            except Exception as exc:
                errors.append(f"{connector.name}:{group['name']}: {exc}")
                if bus:
                    await bus.emit(run_id, "source_finished", node=connector.name,
                                   payload={"group": group["name"], "count": 0,
                                            "errors": 1, "error": str(exc)[:200]})
                return
        for a in res.articles:
            a.query_group = a.query_group or group.get("name", "")
            a.subject_brand = a.subject_brand or _subject_for(group, a, brand)
        collected.extend(res.articles)
        errors.extend(res.errors)
        per_source[connector.name] = per_source.get(connector.name, 0) + len(res.articles)
        if bus:
            await bus.emit(run_id, "source_finished", node=connector.name,
                           payload={"group": group["name"], "count": len(res.articles),
                                    "errors": len(res.errors),
                                    "elapsed_ms": int((time.monotonic() - started) * 1000)})

    tasks = [run_one(c, g) for g in query_groups for c in enabled_connectors()]
    await asyncio.gather(*tasks)

    unique, syndication = dedupe(collected)
    unique = _within_window(unique, days_back)   # HARD date cut — source filters are best-effort
    all_queries = [q for g in query_groups for q in g.get("queries", [])]
    kept = await relevancy_filter(unique, brand, all_queries)

    payload = {
        "articles": [a.model_dump(mode="json") for a in kept],
        "syndication": syndication,
        "stats": {
            "raw": len(collected),
            "unique": len(unique),
            "kept": len(kept),
            "per_source": per_source,
            "errors": errors,
        },
    }
    await _persist_source_file(session_id, payload)
    log.info("ingestion.done", session=session_id,
             **{k: v for k, v in payload["stats"].items() if k != "errors"},
             errors=len(errors))
    return payload["stats"]
