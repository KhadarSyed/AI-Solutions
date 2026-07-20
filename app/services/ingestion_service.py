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
            if len(a.content) > len(keeper.content):  # keep richest copy
                syndication[fp].append(keeper.url)
                syndication[fp].remove(a.url)
                by_fp[fp] = a
            continue
        by_fp[fp] = a

    return list(by_fp.values()), syndication


async def relevancy_filter(
    articles: list[RawArticle], brand: str, queries: list[str], threshold: float = 0.35
) -> list[RawArticle]:
    """Embedding-cosine cut against the brand/query centroid. Skipped (all kept)
    when embedding keys are absent so free-tier runs still work."""
    s = get_settings()
    if not (s.azure_openai_api_key and s.azure_openai_endpoint) or not articles:
        return articles

    from app.llm_gateway.embeddings import embed, embed_one

    try:
        anchor = await embed_one(f"{brand} — " + "; ".join(queries[:10]))
        texts = [f"{a.title}. {a.content[:400]}" for a in articles]
        vectors = await embed(texts)
    except Exception as exc:
        # embeddings are optional here — a bad deployment/key must not fail collection
        log.warning("ingestion.relevancy_skipped", error=str(exc)[:160])
        return articles

    def cos(u: list[float], v: list[float]) -> float:
        dot = sum(x * y for x, y in zip(u, v, strict=True))
        nu = sum(x * x for x in u) ** 0.5
        nv = sum(x * x for x in v) ** 0.5
        return dot / (nu * nv) if nu and nv else 0.0

    kept = [a for a, vec in zip(articles, vectors, strict=True) if cos(anchor, vec) >= threshold]
    log.info("ingestion.relevancy", before=len(articles), after=len(kept))
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
