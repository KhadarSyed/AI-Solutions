# Competitor-aware WebSearch + Sources Orchestrator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the primary (email/Teams) monitoring path search the brand **plus 5 competitors plus industry**, collect from **all** real sources concurrently (incl. Apify + XPOz social), attribute every article to a subject, decode real publishers, and gate inbound email by subject — fixing the root cause of BeOne-only charts.

**Architecture:** Keep the LangGraph pipeline; turn the sequential `ingestion_service.collect()` loop into a concurrent, capabilities-aware **Sources Orchestrator** that fans out `(connector × query-group)` under a semaphore with per-source status events and per-source isolation. A `competitor_agent` resolves 5 competitors (auto-research by default), a `query_plan` builder produces Brand/Competitors/Industry groups, connectors stamp `subject_brand`, and Google-News URLs are decoded to the real outlet.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2 + Alembic, httpx, feedparser, `mcp` streamable-http client, pytest + pytest-asyncio.

## Global Constraints

- **Context isolation (verified, must not regress):** per-request state only in LangGraph state (`thread_id`), the `current_run_id` ContextVar, or DB rows. **No module-level mutable per-request state.**
- **Connectors switch on by env keys** — `enabled()` returns a bool from `get_settings()`.
- **Every connector normalizes to `RawArticle`** (app/tools/connectors/base.py) and declares `Capabilities`.
- **External sources are best-effort:** a source's failure/timeout is isolated to its `ConnectorResult.errors` and never aborts the run.
- **Competitors on by default** (5), skipped only when the user explicitly names competitors.
- **Lint/tests must pass:** `uv run ruff check .` and `uv run pytest -q`.
- Windows dev: tests already use `WindowsSelectorEventLoopPolicy` (tests/conftest.py) — don't change loop policy.

---

### Task 1: Extend `RawArticle` with `subject_brand` and `medium`

**Files:**
- Modify: `app/tools/connectors/base.py:11-32`
- Test: `tests/unit/test_raw_article.py`

**Interfaces:**
- Produces: `RawArticle.subject_brand: str = ""` (the brand/competitor a query targeted), `RawArticle.medium: str = "news"` (`"news"` | `"social"`).

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_raw_article.py
from app.tools.connectors.base import RawArticle

def test_raw_article_has_subject_and_medium_defaults():
    a = RawArticle(title="t", url="https://x.com/a")
    assert a.subject_brand == ""
    assert a.medium == "news"

def test_raw_article_accepts_social_medium():
    a = RawArticle(title="t", url="https://reddit.com/p", medium="social", subject_brand="BeOne")
    assert a.medium == "social"
    assert a.subject_brand == "BeOne"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_raw_article.py -v`
Expected: FAIL (`subject_brand`/`medium` not attributes / unexpected kwarg).

- [ ] **Step 3: Add the fields**
In `app/tools/connectors/base.py`, inside `RawArticle`, after the `language` field (line 22) add:
```python
    # attribution / classification
    subject_brand: str = ""       # the brand or competitor the query targeted
    medium: str = "news"          # "news" | "social"
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_raw_article.py -v`  → Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add app/tools/connectors/base.py tests/unit/test_raw_article.py
git commit -m "feat: RawArticle gains subject_brand + medium"
```

---

### Task 2: Add `Project.competitors` column (model + Alembic migration)

**Files:**
- Modify: `app/db/models.py` (Project class, ~line 57-71)
- Create: `alembic/versions/<autogen>_project_competitors.py` (via autogenerate)
- Test: `tests/unit/test_project_competitors.py`

**Interfaces:**
- Produces: `Project.competitors: Mapped[list]` (JSONB, default `[]`).

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_project_competitors.py
from app.db.models import Project

def test_project_model_has_competitors_column():
    assert "competitors" in Project.__table__.columns
    col = Project.__table__.columns["competitors"]
    assert col.nullable is False
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_project_competitors.py -v` → Expected: FAIL (KeyError `competitors`).

- [ ] **Step 3: Add the column**
In `app/db/models.py`, in `Project`, after `stakeholder_emails` add:
```python
    competitors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
```
(`JSONB` and `text` are already imported in this module — confirm at top; if not, add `from sqlalchemy import text` and `from sqlalchemy.dialects.postgresql import JSONB`.)

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_project_competitors.py -v` → Expected: PASS

- [ ] **Step 5: Generate + apply the migration**
```bash
docker compose up -d postgres
uv run alembic revision --autogenerate -m "add project.competitors"
uv run alembic upgrade head
```
Open the generated file; verify it contains `op.add_column('projects', sa.Column('competitors', ...))` and nothing unrelated (drop any spurious autogen ops for the LangGraph checkpointer tables).

- [ ] **Step 6: Commit**
```bash
git add app/db/models.py alembic/versions/
git commit -m "feat: add Project.competitors column + migration"
```

---

### Task 3: Google News URL decode → real publisher/domain

**Files:**
- Create: `app/tools/scraping/gnews.py`
- Modify: `app/tools/connectors/google_news_rss.py:25-53`
- Test: `tests/unit/test_gnews_decode.py`

**Interfaces:**
- Produces: `gnews.decode(url: str) -> tuple[str, str] | None` returning `(real_url, publisher_domain)` or `None`; `gnews.is_gnews_url(url: str) -> bool`.
- Consumes (in connector): `_entry_to_article` uses `decode()` to replace `news.google.com` with the real outlet.

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_gnews_decode.py
from app.tools.scraping import gnews

def test_is_gnews_url():
    assert gnews.is_gnews_url("https://news.google.com/rss/articles/CBMiAbc")
    assert not gnews.is_gnews_url("https://economictimes.com/x")

def test_decode_non_gnews_returns_none():
    assert gnews.decode("https://economictimes.com/x") is None

def test_decode_extracts_domain_from_base64_url(monkeypatch):
    # a CBMi payload whose decoded bytes embed a real URL
    import base64
    real = "https://www.reuters.com/world/story-123"
    # emulate the common encoding: bytes contain the URL as a length-prefixed field
    payload = b"\x08\x13\x12" + bytes([len(real)]) + real.encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    url = f"https://news.google.com/rss/articles/CBMi{token}"
    out = gnews.decode(url)
    assert out is not None
    real_url, domain = out
    assert "reuters.com" in domain
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_gnews_decode.py -v` → Expected: FAIL (module missing).

- [ ] **Step 3: Implement the decoder**
```python
# app/tools/scraping/gnews.py
"""Decode Google News redirect URLs to the real article URL + publisher domain.

Google News RSS links look like
  https://news.google.com/rss/articles/CBMi<base64url-payload>...
The payload is a protobuf-ish blob; the article URL appears as a UTF-8 run
inside it. We extract the first http(s) URL we can find; if that fails we fall
back to following the redirect. Best-effort — returns None when we can't resolve.
"""
import base64
import re
from urllib.parse import urlparse

_GNEWS_HOST = "news.google.com"
_URL_RE = re.compile(rb"https?://[^\s\"'<>\\]+")


def is_gnews_url(url: str) -> bool:
    return _GNEWS_HOST in (url or "")


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def decode(url: str) -> tuple[str, str] | None:
    """(real_url, publisher_domain) or None. Offline base64 parse only."""
    if not is_gnews_url(url):
        return None
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", url)
    if not m:
        return None
    token = m.group(1)
    # strip a leading "CBMi"/type tag if present, pad for base64url
    for candidate in (token, token[4:] if token.startswith("CBMi") else token):
        with_pad = candidate + "=" * (-len(candidate) % 4)
        try:
            raw = base64.urlsafe_b64decode(with_pad)
        except Exception:
            continue
        found = _URL_RE.search(raw)
        if found:
            real = found.group(0).decode("utf-8", "ignore")
            # trim trailing protobuf bytes that aren't URL-legal
            real = re.split(r"[^\w:/.?=&%#\-]", real, maxsplit=1)[0]
            if real.startswith("http") and _GNEWS_HOST not in real:
                return real, _domain(real)
    return None


async def resolve_via_redirect(url: str, timeout: float = 8.0) -> tuple[str, str] | None:
    """Fallback: follow the redirect to capture the final outlet URL."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        final = str(resp.url)
        if _GNEWS_HOST not in final:
            return final, _domain(final)
    except Exception:
        return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_gnews_decode.py -v` → Expected: PASS

- [ ] **Step 5: Wire decode into the connector**
In `app/tools/connectors/google_news_rss.py`, replace `_entry_to_article` body so the URL/domain use the decode when the link is a gnews URL:
```python
def _entry_to_article(entry, query: str, group: str) -> RawArticle:
    from app.tools.scraping import gnews

    publisher = ""
    if getattr(entry, "source", None) is not None:
        publisher = getattr(entry.source, "title", "") or ""
    link = getattr(entry, "link", "") or ""
    domain = urlparse(link).netloc.removeprefix("www.")
    decoded = gnews.decode(link)          # offline decode; redirect fallback done in orchestrator
    if decoded:
        link, domain = decoded
    published: date | None = None
    if getattr(entry, "published_parsed", None):
        published = datetime(*entry.published_parsed[:6]).date()
    title = getattr(entry, "title", "") or ""
    if publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(f" - {publisher}")]
    return RawArticle(
        publisher_name=publisher,
        title=title,
        content=getattr(entry, "summary", "") or "",
        publisher_domain=domain,
        published_date=published,
        url=link,
        language="en",
        source="google_news_rss",
        query_group=group,
        original_query=query,
    )
```

- [ ] **Step 6: Run the connector's existing tests + lint**
Run: `uv run pytest tests/unit/test_gnews_decode.py -v && uv run ruff check app/tools/scraping/gnews.py app/tools/connectors/google_news_rss.py`
Expected: PASS / clean.

- [ ] **Step 7: Commit**
```bash
git add app/tools/scraping/gnews.py app/tools/connectors/google_news_rss.py tests/unit/test_gnews_decode.py
git commit -m "feat: decode Google News URLs to real publisher/domain"
```

---

### Task 4: Competitor resolution agent

**Files:**
- Create: `app/agents/competitor_agent.py`
- Test: `tests/unit/test_competitor_agent.py`

**Interfaces:**
- Produces: `async resolve_competitors(brand: str, project_id: str, override: list[str] | None = None) -> list[str]` (≤5).

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_competitor_agent.py
import pytest
from app.agents import competitor_agent as ca

@pytest.mark.asyncio
async def test_override_wins_and_caps_at_5(monkeypatch):
    called = {"research": False}
    async def _no_research(*a, **k):
        called["research"] = True
        return []
    monkeypatch.setattr(ca, "_research", _no_research)
    out = await ca.resolve_competitors("BeOne", "p1",
                                       override=["A", "B", "C", "D", "E", "F"])
    assert out == ["A", "B", "C", "D", "E"]
    assert called["research"] is False

@pytest.mark.asyncio
async def test_auto_research_when_no_override(monkeypatch):
    async def _research(brand, project_id):
        return ["Carrier", "Daikin", "Trane", "Lennox", "Johnson Controls"]
    monkeypatch.setattr(ca, "_research", _research)
    monkeypatch.setattr(ca, "_from_project", lambda pid: [])
    monkeypatch.setattr(ca, "_from_memory", _empty_async)
    out = await ca.resolve_competitors("BeOne", "p1")
    assert out[0] == "Carrier"
    assert len(out) == 5

async def _empty_async(*a, **k):
    return []
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_competitor_agent.py -v` → Expected: FAIL (module missing).

- [ ] **Step 3: Implement the agent**
```python
# app/agents/competitor_agent.py
"""Resolve up to 5 competitors for a brand. Default: auto-research; skipped only
when the user explicitly names competitors. Caches research to Project.competitors
and Mem0 SEMANTIC (also seeds the previously-empty semantic memory type)."""
import contextlib
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.db.base import get_sessionmaker
from app.db.models import Project
from app.llm_gateway.guarded import GuardedAgent
from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger

log = get_logger(__name__)
MAX_COMPETITORS = 5


class _Competitors(BaseModel):
    competitors: list[str] = Field(description="direct competitor brand names, most direct first")


async def _from_project(project_id: str) -> list[str]:
    async with get_sessionmaker()() as db:
        p = await db.get(Project, uuid.UUID(project_id))
        return list(p.competitors or []) if p else []


async def _from_memory(brand: str, project_id: str) -> list[str]:
    with contextlib.suppress(Exception):
        hits = await recall(agent="competitor", query=f"{brand} competitors",
                            project_id=project_id, memory_type=MemoryType.SEMANTIC, limit=1)
        for h in hits:
            names = (h.get("metadata") or {}).get("competitors")
            if names:
                return list(names)
    return []


async def _research(brand: str, project_id: str) -> list[str]:
    agent = GuardedAgent(
        purpose="competitor_research", stage="query_builder",
        system_prompt=(
            "Name the 5 most direct competitors of the given brand (same industry, "
            "comparable products). Return brand names only, most direct first."),
        output_type=_Competitors, temperature=0.0, cacheable=True,
    )
    res = await agent.run(f"Brand: {brand}")
    return res.competitors


async def _cache(brand: str, project_id: str, names: list[str]) -> None:
    with contextlib.suppress(Exception):
        async with get_sessionmaker()() as db, db.begin():
            await db.execute(update(Project).where(Project.id == uuid.UUID(project_id))
                             .values(competitors=names))
    with contextlib.suppress(Exception):
        await remember(agent="competitor", memory_type=MemoryType.SEMANTIC,
                       project_id=project_id, content=f"{brand} competitors: {', '.join(names)}",
                       metadata={"competitors": names})


async def resolve_competitors(brand: str, project_id: str,
                              override: list[str] | None = None) -> list[str]:
    if override:
        return override[:MAX_COMPETITORS]
    stored = await _from_project(project_id)
    if stored:
        return stored[:MAX_COMPETITORS]
    remembered = await _from_memory(brand, project_id)
    if remembered:
        return remembered[:MAX_COMPETITORS]
    try:
        names = (await _research(brand, project_id))[:MAX_COMPETITORS]
    except Exception as exc:
        log.warning("competitor.research_failed", brand=brand, error=str(exc)[:150])
        return []
    if names:
        await _cache(brand, project_id, names)
    return names
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_competitor_agent.py -v` → Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add app/agents/competitor_agent.py tests/unit/test_competitor_agent.py
git commit -m "feat: competitor resolution agent (auto-research + cache)"
```

---

### Task 5: Query-plan builder

**Files:**
- Create: `app/services/query_plan.py`
- Test: `tests/unit/test_query_plan.py`

**Interfaces:**
- Produces: `build_query_plan(brand: str, competitors: list[str], industry: str | None) -> list[dict]` where each group = `{"name": str, "queries": list[str], "subject": str | None, "subject_per_query": bool}`.

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_query_plan.py
from app.services.query_plan import build_query_plan

def test_plan_has_brand_and_competitor_groups():
    plan = build_query_plan("BeOne", ["Carrier", "Daikin"], industry="HVAC")
    names = [g["name"] for g in plan]
    assert names == ["Brand News", "Competitors News", "Industry News"]
    brand = plan[0]
    assert brand["queries"] == ["BeOne"] and brand["subject"] == "BeOne"
    comp = plan[1]
    assert comp["queries"] == ["Carrier", "Daikin"] and comp["subject_per_query"] is True

def test_industry_group_skipped_when_empty():
    plan = build_query_plan("BeOne", ["Carrier"], industry=None)
    assert [g["name"] for g in plan] == ["Brand News", "Competitors News"]

def test_no_competitors_still_has_brand():
    plan = build_query_plan("BeOne", [], industry=None)
    assert [g["name"] for g in plan] == ["Brand News"]
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_query_plan.py -v` → Expected: FAIL (module missing).

- [ ] **Step 3: Implement**
```python
# app/services/query_plan.py
"""Build the Brand / Competitors / Industry query groups from resolved inputs."""


def build_query_plan(brand: str, competitors: list[str],
                     industry: str | None) -> list[dict]:
    plan: list[dict] = [
        {"name": "Brand News", "queries": [brand], "subject": brand,
         "subject_per_query": False},
    ]
    if competitors:
        plan.append({"name": "Competitors News", "queries": list(competitors),
                     "subject": None, "subject_per_query": True})
    if industry and industry.strip():
        plan.append({"name": "Industry News", "queries": [f"{industry} industry"],
                     "subject": "", "subject_per_query": False})
    return plan
```

- [ ] **Step 4: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_query_plan.py -v` → Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add app/services/query_plan.py tests/unit/test_query_plan.py
git commit -m "feat: query-plan builder (brand + competitors + industry)"
```

---

### Task 6: Real Apify connector (2 actors)

**Files:**
- Modify: `app/tools/connectors/keyed.py:120-130` (ApifyConnector)
- Modify: `app/config/settings.py` (add actor ids)
- Test: `tests/unit/test_apify_connector.py`

**Interfaces:**
- Consumes: `settings.apify_token`, `settings.apify_actor_search` (default `"apify/google-search-scraper"`), `settings.apify_actor_rag` (default `"apify/rag-web-browser"`).
- Produces: `ApifyConnector.enabled()` → `bool(settings.apify_token)`; normalizes dataset items → `RawArticle(source="apify", medium="news")`.

- [ ] **Step 1: Add settings**
In `app/config/settings.py`, near the other connector keys:
```python
    apify_actor_search: str = "apify/google-search-scraper"
    apify_actor_rag: str = "apify/rag-web-browser"
```

- [ ] **Step 2: Write the failing test**
```python
# tests/unit/test_apify_connector.py
import pytest
from app.tools.connectors.base import SearchFilters
from app.tools.connectors.keyed import ApifyConnector

@pytest.mark.asyncio
async def test_apify_normalizes_dataset_items(monkeypatch):
    conn = ApifyConnector()
    async def fake_run(actor, query, filters):
        return [{"title": "BeOne wins award", "url": "https://ex.com/a",
                 "description": "body", "loadedUrl": "https://ex.com/a"}]
    monkeypatch.setattr(conn, "_run_actor", fake_run)
    monkeypatch.setattr("app.tools.connectors.keyed.get_settings",
                        lambda: type("S", (), {"apify_token": "x",
                        "apify_actor_search": "a/s", "apify_actor_rag": "a/r"})())
    res = await conn.search(["BeOne"], SearchFilters())
    assert res.articles and res.articles[0].source == "apify"
    assert res.articles[0].publisher_domain == "ex.com"
```

- [ ] **Step 3: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_apify_connector.py -v` → Expected: FAIL (stub returns errors).

- [ ] **Step 4: Implement**
Replace the `ApifyConnector` class in `app/tools/connectors/keyed.py`:
```python
class ApifyConnector(Connector):
    name = "apify"
    capabilities = Capabilities(max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().apify_token)

    async def _run_actor(self, actor: str, query: str, filters: SearchFilters) -> list[dict]:
        s = get_settings()
        url = f"https://api.apify.com/v2/acts/{actor.replace('/', '~')}/run-sync-get-dataset-items"
        body = {"queries": query, "maxResults": filters.max_results,
                "query": query, "maxRequestsPerCrawl": filters.max_results}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, params={"token": s.apify_token}, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        s = get_settings()

        async def one(actor: str, query: str) -> None:
            try:
                for item in await self._run_actor(actor, query, filters):
                    url = item.get("url") or item.get("loadedUrl") or item.get("link", "")
                    if not url:
                        continue
                    result.articles.append(RawArticle(
                        publisher_name=item.get("source") or item.get("displayedUrl", "") or "",
                        title=item.get("title", "") or "",
                        content=item.get("description") or item.get("text") or item.get("snippet", "") or "",
                        publisher_domain=urlparse(url).netloc.removeprefix("www."),
                        url=url, source=self.name, medium="news", original_query=query,
                    ))
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        tasks = [one(a, q) for q in queries for a in (s.apify_actor_search, s.apify_actor_rag)]
        await asyncio.gather(*tasks)
        return result
```

- [ ] **Step 5: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_apify_connector.py -v` → Expected: PASS

- [ ] **Step 6: Commit**
```bash
git add app/tools/connectors/keyed.py app/config/settings.py tests/unit/test_apify_connector.py
git commit -m "feat: real Apify connector (google-search + rag-web-browser actors)"
```

---

### Task 7: XPOz social connector (Reddit + TikTok via MCP)

**Files:**
- Create: `app/tools/connectors/xpoz.py`
- Modify: `app/config/settings.py` (add `xpoz_mcp_url`)
- Modify: `app/services/ingestion_service.py:19-37` (import + register from new module)
- Test: `tests/unit/test_xpoz_connector.py`

**Interfaces:**
- Consumes: `settings.xpoz_api_key`, `settings.xpoz_mcp_url` (default `"https://mcp.xpoz.ai/mcp"`).
- Produces: `XPozConnector.enabled()` → `bool(settings.xpoz_api_key)`; normalizes Reddit/TikTok posts → `RawArticle(medium="social", source="reddit"|"tiktok")`.

- [ ] **Step 1: Add setting**
In `app/config/settings.py`:
```python
    xpoz_mcp_url: str = "https://mcp.xpoz.ai/mcp"
```

- [ ] **Step 2: Write the failing test**
```python
# tests/unit/test_xpoz_connector.py
import pytest
from app.tools.connectors.base import SearchFilters
from app.tools.connectors.xpoz import XPozConnector, _reddit_to_article, _tiktok_to_article

def test_reddit_normalization():
    a = _reddit_to_article({"title": "BeOne buzz", "selftext": "body",
        "permalink": "/r/x/abc", "author": "u1", "subreddit": "x"}, "BeOne")
    assert a.medium == "social" and a.source == "reddit"
    assert a.publisher_domain == "reddit.com" and a.author == "u1"
    assert a.subject_brand == "BeOne" and "reddit.com" in a.url

def test_tiktok_normalization():
    a = _tiktok_to_article({"desc": "BeOne clip", "id": "123",
        "author": {"uniqueId": "creator"}, "webVideoUrl": "https://tiktok.com/@c/video/123"}, "BeOne")
    assert a.medium == "social" and a.source == "tiktok" and a.author == "@creator"

@pytest.mark.asyncio
async def test_search_disabled_without_key(monkeypatch):
    monkeypatch.setattr("app.tools.connectors.xpoz.get_settings",
                        lambda: type("S", (), {"xpoz_api_key": "", "xpoz_mcp_url": "u"})())
    assert XPozConnector().enabled() is False
```

- [ ] **Step 3: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_xpoz_connector.py -v` → Expected: FAIL (module missing).

- [ ] **Step 4: Implement**
```python
# app/tools/connectors/xpoz.py
"""XPOz social-listening connector (Reddit + TikTok) over its MCP server.

Static Bearer auth (no OAuth). Normalizes posts to RawArticle with medium="social"
so social coverage enriches the corpus without skewing news-only charts."""
import asyncio
import contextlib
import json
from datetime import timedelta

from app.config.settings import get_settings
from app.observability.logging import get_logger
from app.tools.connectors.base import (
    Capabilities, Connector, ConnectorResult, RawArticle, SearchFilters,
)

log = get_logger(__name__)


def _reddit_to_article(post: dict, subject: str) -> RawArticle:
    permalink = post.get("permalink", "") or ""
    url = permalink if permalink.startswith("http") else f"https://reddit.com{permalink}"
    return RawArticle(
        publisher_name=f"Reddit — r/{post.get('subreddit', '')}".rstrip(" —"),
        title=post.get("title", "") or (post.get("selftext", "") or "")[:120],
        content=post.get("selftext", "") or "",
        publisher_domain="reddit.com", url=url, author=post.get("author", "") or "",
        source="reddit", medium="social", subject_brand=subject, original_query=subject,
    )


def _tiktok_to_article(post: dict, subject: str) -> RawArticle:
    author = (post.get("author") or {})
    handle = author.get("uniqueId") or author.get("nickname") or ""
    url = post.get("webVideoUrl") or post.get("url", "") or ""
    return RawArticle(
        publisher_name="TikTok", title=(post.get("desc", "") or "")[:120],
        content=post.get("desc", "") or "", publisher_domain="tiktok.com",
        url=url or f"https://tiktok.com/video/{post.get('id', '')}",
        author=f"@{handle}" if handle else "", source="tiktok", medium="social",
        subject_brand=subject, original_query=subject,
    )


class XPozConnector(Connector):
    name = "xpoz"
    capabilities = Capabilities(date_range=True, max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().xpoz_api_key)

    async def _call(self, tool: str, args: dict) -> list[dict]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        s = get_settings()
        async with streamablehttp_client(
                s.xpoz_mcp_url, headers={"Authorization": f"Bearer {s.xpoz_api_key}"},
                timeout=timedelta(seconds=40), sse_read_timeout=timedelta(seconds=40)) as (r, w, *_), \
                ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(tool, args, read_timeout_seconds=timedelta(seconds=40))
        text = "".join(getattr(i, "text", "") or "" for i in res.content)
        with contextlib.suppress(Exception):
            data = json.loads(text)
            if isinstance(data, dict):
                return data.get("items") or data.get("results") or data.get("data") or []
            if isinstance(data, list):
                return data
        return []

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        limit = min(filters.max_results, 25)

        async def one(query: str) -> None:
            try:
                reddit = await self._call("getRedditPostsByKeywords",
                    {"query": query, "limit": limit, "responseType": "full"})
                for p in reddit:
                    result.articles.append(_reddit_to_article(p, query))
            except Exception as exc:
                result.errors.append(f"{self.name}:reddit:{query}: {exc}")
            try:
                tiktok = await self._call("getTiktokPostsByKeywords",
                    {"query": query, "limit": limit, "responseType": "full"})
                for p in tiktok:
                    result.articles.append(_tiktok_to_article(p, query))
            except Exception as exc:
                result.errors.append(f"{self.name}:tiktok:{query}: {exc}")

        await asyncio.gather(*(one(q) for q in queries))
        log.info("connector.xpoz", queries=len(queries), articles=len(result.articles),
                 errors=len(result.errors))
        return result
```

- [ ] **Step 5: Register the connector**
In `app/services/ingestion_service.py`, remove `XPozConnector` from the `keyed` import (line 19-24) and instead `from app.tools.connectors.xpoz import XPozConnector`. Keep it in `ALL_CONNECTORS` (line 36). Delete the old stub `XPozConnector` class from `keyed.py:133-143`.

- [ ] **Step 6: Run tests + lint**
Run: `uv run pytest tests/unit/test_xpoz_connector.py -v && uv run ruff check app/tools/connectors/xpoz.py`
Expected: PASS / clean.

- [ ] **Step 7: Commit**
```bash
git add app/tools/connectors/xpoz.py app/config/settings.py app/services/ingestion_service.py tests/unit/test_xpoz_connector.py
git commit -m "feat: XPOz social connector (Reddit+TikTok via MCP, medium=social)"
```

---

### Task 8: Concurrent Sources Orchestrator (refactor `collect`)

**Files:**
- Modify: `app/services/ingestion_service.py:112-169` (`collect`)
- Modify: `app/config/settings.py` (add `source_concurrency: int = 8`)
- Modify: `app/orchestration/graphs/pipeline_graph.py` (collect node passes `run_id`)
- Test: `tests/unit/test_orchestrator.py`

**Interfaces:**
- Consumes: `enabled_connectors()`, `dedupe`, `relevancy_filter`, `get_event_bus().emit(run_id, event_type, node=, payload=)`, `SearchFilters`, query groups from Task 5.
- Produces: `collect(..., run_id: str | None = None)` — concurrent fan-out over `(connector × group)`, per-source `source_started`/`source_finished` events, `subject_brand` stamped per article.

- [ ] **Step 1: Add setting**
`app/config/settings.py`: `source_concurrency: int = 8`

- [ ] **Step 2: Write the failing test**
```python
# tests/unit/test_orchestrator.py
import pytest
from app.tools.connectors.base import ConnectorResult, RawArticle
import app.services.ingestion_service as ing

class _FakeConn:
    def __init__(self, name, fail=False):
        self.name = name; self._fail = fail
    def enabled(self): return True
    async def search(self, queries, filters):
        if self._fail:
            raise RuntimeError("boom")
        return ConnectorResult(articles=[
            RawArticle(title=f"{q} via {self.name}", url=f"https://{self.name}.com/{q}",
                       original_query=q) for q in queries])

@pytest.mark.asyncio
async def test_fanout_isolates_failing_source_and_stamps_subject(monkeypatch):
    monkeypatch.setattr(ing, "enabled_connectors", lambda: [_FakeConn("ok"), _FakeConn("bad", fail=True)])
    async def _no_persist(*a, **k): return None
    monkeypatch.setattr(ing, "_persist_source_file", _no_persist)
    monkeypatch.setattr(ing, "relevancy_filter", lambda arts, *a, **k: _ident(arts))
    plan = [{"name": "Competitors News", "queries": ["Carrier"], "subject": None, "subject_per_query": True}]
    stats = await ing.collect(session_id="00000000-0000-0000-0000-000000000001",
                              brand="BeOne", query_groups=plan)
    assert stats["per_source"]["ok"] == 1
    assert any("bad" in e for e in stats["errors"])

async def _ident(arts, *a, **k): return arts
```

- [ ] **Step 3: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_orchestrator.py -v` → Expected: FAIL (sequential `collect`, no isolation/subject, `_persist_source_file` missing).

- [ ] **Step 4: Refactor `collect`**
Replace `collect` (and extract persistence) in `app/services/ingestion_service.py`:
```python
def _subject_for(group: dict, article: RawArticle, brand: str) -> str:
    if group.get("subject_per_query"):
        return article.original_query or ""
    subj = group.get("subject")
    return brand if subj is None else subj


async def _persist_source_file(session_id: str, payload: dict) -> None:
    key = keys.source_file(session_id)
    await get_artifact_store().put_json(key, payload)
    async with get_sessionmaker()() as db, db.begin():
        await db.execute(update(SessionRow).where(SessionRow.id == uuid.UUID(session_id))
                         .values(source_file_key=key, articles_count=payload["stats"]["kept"],
                                 status="ingested"))


async def collect(*, session_id: str, brand: str, query_groups: list[dict],
                  days_back: int | None = None, language: str = "en",
                  country: str | None = None, max_per_query: int = 50,
                  extra_articles: list[RawArticle] | None = None,
                  run_id: str | None = None) -> dict:
    import asyncio
    from app.orchestration.events import get_event_bus

    if days_back is None:
        days_back = get_settings().collection_days_back
    filters = SearchFilters(days_back=days_back, language=language, country=country,
                            max_results=max_per_query)
    sem = asyncio.Semaphore(get_settings().source_concurrency)
    bus = get_event_bus() if run_id else None

    collected: list[RawArticle] = list(extra_articles or [])
    errors: list[str] = []
    per_source: dict[str, int] = {"file_upload": len(collected)} if collected else {}

    async def run_one(connector, group) -> None:
        import time
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

    payload = {"articles": [a.model_dump(mode="json") for a in kept],
               "syndication": syndication,
               "stats": {"raw": len(collected), "unique": len(unique), "kept": len(kept),
                         "per_source": per_source, "errors": errors}}
    await _persist_source_file(session_id, payload)
    log.info("ingestion.done", session=session_id,
             **{k: v for k, v in payload["stats"].items() if k != "errors"},
             errors=len(errors))
    return payload["stats"]
```

- [ ] **Step 5: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_orchestrator.py -v` → Expected: PASS

- [ ] **Step 6: Pass `run_id` from the pipeline collect node**
In `app/orchestration/graphs/pipeline_graph.py` `collect` node, change the `ingestion_service.collect(...)` call to include `run_id=state.get("run_id")`.

- [ ] **Step 7: Full unit run + lint**
Run: `uv run pytest tests/unit -q && uv run ruff check app/services/ingestion_service.py`
Expected: PASS / clean.

- [ ] **Step 8: Commit**
```bash
git add app/services/ingestion_service.py app/config/settings.py app/orchestration/graphs/pipeline_graph.py tests/unit/test_orchestrator.py
git commit -m "feat: concurrent Sources Orchestrator with per-source status + subject attribution"
```

---

### Task 9: Email start path — competitors + query plan; inbound subject gating

**Files:**
- Modify: `app/agents/email_agent.py` (`_start_run` ~156-182, `handle_inbound` ~185-190)
- Test: `tests/unit/test_email_gating.py`

**Interfaces:**
- Consumes: `resolve_competitors` (Task 4), `build_query_plan` (Task 5), `Project.industry`.
- Produces: `subject_allowed(subject: str, brands: list[str]) -> bool`; `_start_run` builds a competitor-aware plan.

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_email_gating.py
from app.agents.email_agent import subject_allowed

def test_start_subject_allowed():
    assert subject_allowed("Start BeOne monitoring", ["BeOne", "Trane"])
    assert subject_allowed("Re: [BEONE-20260720-001] approve", ["BeOne"])

def test_unrelated_subject_ignored():
    assert not subject_allowed("Your invoice is due", ["BeOne", "Trane"])
    assert not subject_allowed("", ["BeOne"])
```

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run pytest tests/unit/test_email_gating.py -v` → Expected: FAIL (function missing).

- [ ] **Step 3: Add `subject_allowed` and gate `handle_inbound`**
In `app/agents/email_agent.py` add:
```python
import re as _re

_TASK_TAG = _re.compile(r"\[[A-Z0-9]+-\d{8}-\d{3}\]")
_START = _re.compile(r"\b(start|run|begin|launch|monitor|track)\b", _re.I)


def subject_allowed(subject: str, brands: list[str]) -> bool:
    s = subject or ""
    if _TASK_TAG.search(s):
        return True
    low = s.lower()
    return bool(_START.search(low) and any(b.lower() in low for b in brands if b))
```
At the top of `handle_inbound`, after computing `project`/`sender_verified` for a NEW task (i.e., email channel), gate on subject. Insert right after the `project is None or not sender_verified` guard:
```python
    if inbound.channel == "email":
        brands = [p.brand_name for p in await _all_brands()]
        if not subject_allowed(inbound.subject, brands):
            log.info("inbound.subject_unmatched", subject=inbound.subject[:80])
            return {"handled": False, "reason": "subject not recognized for this system"}
```
Add the helper:
```python
async def _all_brands() -> list:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(Project))).scalars().all()
```

- [ ] **Step 4: Make `_start_run` competitor-aware**
Replace the query-group construction in `_start_run` (lines ~162-168) with:
```python
    from app.agents.competitor_agent import resolve_competitors
    from app.services.query_plan import build_query_plan

    brand = brand or project.brand_name
    override = _parse_competitor_override(inbound.text)   # "competitors: A, B" → [...]
    competitors = await resolve_competitors(brand, str(project.id), override=override)
    query_groups = build_query_plan(brand, competitors, project.industry)
```
Add the parser:
```python
def _parse_competitor_override(text: str) -> list[str] | None:
    m = _re.search(r"competitors?\s*[:\-]\s*(.+)", text or "", _re.I)
    if not m:
        return None
    return [c.strip() for c in _re.split(r"[,;]", m.group(1)) if c.strip()][:5]
```

- [ ] **Step 5: Run test to verify it passes**
Run: `uv run pytest tests/unit/test_email_gating.py -v` → Expected: PASS

- [ ] **Step 6: Lint**
Run: `uv run ruff check app/agents/email_agent.py` → Expected: clean.

- [ ] **Step 7: Commit**
```bash
git add app/agents/email_agent.py tests/unit/test_email_gating.py
git commit -m "feat: competitor-aware email start + inbound subject gating"
```

---

### Task 10: Concurrency / context-isolation integration test

**Files:**
- Test: `tests/integration/test_concurrent_isolation.py`

**Interfaces:**
- Consumes: `collect` (Task 8), `current_run_id` ContextVar.

- [ ] **Step 1: Write the test**
```python
# tests/integration/test_concurrent_isolation.py
import asyncio
import pytest
from app.orchestration.context import current_run_id
import app.services.ingestion_service as ing
from app.tools.connectors.base import ConnectorResult, RawArticle

class _EchoConn:
    name = "echo"
    def enabled(self): return True
    async def search(self, queries, filters):
        await asyncio.sleep(0.01)  # force interleaving
        return ConnectorResult(articles=[
            RawArticle(title=q, url=f"https://echo.com/{q}", original_query=q) for q in queries])

@pytest.mark.asyncio
async def test_two_runs_do_not_cross_contaminate(monkeypatch):
    monkeypatch.setattr(ing, "enabled_connectors", lambda: [_EchoConn()])
    captured = {}
    async def _persist(session_id, payload):
        captured[session_id] = payload
    monkeypatch.setattr(ing, "_persist_source_file", _persist)
    async def _ident(arts, *a, **k): return arts
    monkeypatch.setattr(ing, "relevancy_filter", _ident)

    async def run(run_id, session_id, brand, comp):
        current_run_id.set(run_id)
        plan = [{"name": "Competitors News", "queries": [comp], "subject": None, "subject_per_query": True}]
        await ing.collect(session_id=session_id, brand=brand, query_groups=plan, run_id=run_id)

    await asyncio.gather(
        run("r1", "11111111-1111-1111-1111-111111111111", "BeOne", "Carrier"),
        run("r2", "22222222-2222-2222-2222-222222222222", "Trane", "Daikin"),
    )
    a1 = captured["11111111-1111-1111-1111-111111111111"]["articles"]
    a2 = captured["22222222-2222-2222-2222-222222222222"]["articles"]
    assert {a["subject_brand"] for a in a1} == {"Carrier"}
    assert {a["subject_brand"] for a in a2} == {"Daikin"}
```

- [ ] **Step 2: Run it**
Run: `uv run pytest tests/integration/test_concurrent_isolation.py -v` → Expected: PASS (each run's articles carry only its own `subject_brand`).

- [ ] **Step 3: Full suite + lint**
Run: `uv run pytest -q && uv run ruff check .` → Expected: PASS / clean.

- [ ] **Step 4: Commit**
```bash
git add tests/integration/test_concurrent_isolation.py
git commit -m "test: concurrent runs keep per-run subject attribution isolated"
```

---

## Self-Review

**Spec coverage:**
- Competitors on by default (5, auto-research, override) → Task 4 ✓; used in Task 9 ✓; `Project.competitors` → Task 2 ✓
- Query plan (Brand/Competitors/Industry) → Task 5 ✓
- Concurrent capabilities-aware orchestrator + per-source status + isolation → Task 8 ✓
- Apify (2 actors) → Task 6 ✓; XPOz social MCP → Task 7 ✓
- Google News real publisher → Task 3 ✓
- `subject_brand` attribution → Task 1 (field) + Task 8 (stamping) ✓
- Inbound subject gating → Task 9 ✓
- Concurrency/context test → Task 10 ✓
- `medium="social"` for XPOz → Task 1 (field) + Task 7 ✓

**Placeholder scan:** none — every step has runnable code/commands.

**Type consistency:** `resolve_competitors(brand, project_id, override=None)`, `build_query_plan(brand, competitors, industry)`, `collect(..., run_id=None)`, `_subject_for(group, article, brand)`, `subject_allowed(subject, brands)`, `_reddit_to_article(post, subject)`/`_tiktok_to_article(post, subject)` are referenced consistently across tasks.

**Note for the implementer:** `capabilities`-aware source-side filtering (applying only supported `SearchFilters` per connector, post-filtering the rest) already exists implicitly — each connector reads only the filters it supports. If a connector must NOT receive an unsupported filter, add the guard inside that connector, not the orchestrator. Deferred to enrichment sub-project: country/author/logo. XPOz `responseType`/exact result keys should be confirmed against a live `list_tools`/sample during Task 7 (the normalizers tolerate missing keys).
```
