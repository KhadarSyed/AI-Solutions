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
    XPozConnector,
)
from app.tools.connectors.searxng import SearxngConnector

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

    anchor = await embed_one(f"{brand} — " + "; ".join(queries[:10]))
    texts = [f"{a.title}. {a.content[:400]}" for a in articles]
    vectors = await embed(texts)

    def cos(u: list[float], v: list[float]) -> float:
        dot = sum(x * y for x, y in zip(u, v, strict=True))
        nu = sum(x * x for x in u) ** 0.5
        nv = sum(x * x for x in v) ** 0.5
        return dot / (nu * nv) if nu and nv else 0.0

    kept = [a for a, vec in zip(articles, vectors, strict=True) if cos(anchor, vec) >= threshold]
    log.info("ingestion.relevancy", before=len(articles), after=len(kept))
    return kept


async def collect(
    *,
    session_id: str,
    brand: str,
    query_groups: list[dict],
    days_back: int = 7,
    language: str = "en",
    country: str | None = None,
    max_per_query: int = 50,
    extra_articles: list[RawArticle] | None = None,
) -> dict:
    """Run the fleet and persist source_file. query_groups: [{name, queries:[...]}]."""
    filters = SearchFilters(
        days_back=days_back, language=language, country=country, max_results=max_per_query
    )

    collected: list[RawArticle] = list(extra_articles or [])
    errors: list[str] = []
    per_source: dict[str, int] = {"file_upload": len(collected)} if collected else {}

    for group in query_groups:
        queries = group.get("queries", [])
        for connector in enabled_connectors():
            res = await connector.search(queries, filters)
            for a in res.articles:
                a.query_group = a.query_group or group.get("name", "")
            collected.extend(res.articles)
            errors.extend(res.errors)
            per_source[connector.name] = per_source.get(connector.name, 0) + len(res.articles)

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
    key = keys.source_file(session_id)
    await get_artifact_store().put_json(key, payload)

    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            update(SessionRow)
            .where(SessionRow.id == uuid.UUID(session_id))
            .values(source_file_key=key, articles_count=len(kept), status="ingested")
        )
    log.info("ingestion.done", session=session_id, **payload["stats"] | {"errors": len(errors)})
    return payload["stats"]
