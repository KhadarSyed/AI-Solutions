# Sub-project 1 — Competitor-aware WebSearch + Sources Orchestrator Agent

**Status:** proposed · **Date:** 2026-07-20 · **Part of:** the 6-sub-project program to bring PR Intelligence to a 100% best-practice agentic system (this is #1 of 6).

## 1. Problem

On the primary (email/Teams) entry path the system produces degenerate output:

- `_start_run` hardcodes the query to `[{"Brand News": [brand]}]` — **competitors are never searched or attributed**, so Share-of-Voice, PR-Impact, and the competitive matrix collapse to a single brand ([email_agent.py:167-173](../../../app/agents/email_agent.py)).
- Collection is a **sequential loop** (`for query → for connector → await`) in `ingestion_service.collect()` — not concurrent, and not an orchestrating "agent."
- Google News RSS leaves `publisher_domain = news.google.com` for ~20% of rows (the redirect host), so real outlets are lost.
- `apify` and `xpoz` are stubs (`enabled()` hardcoded `False`).

## 2. Goal

A real **WebSearch Agent** that, for every entry path, resolves the brand + up to 5 competitors + industry terms, fans out across **all enabled sources concurrently**, decodes **real publisher names/domains**, attributes each article to a specific subject (brand or a named competitor), and hands a typed envelope to the Tagging Agent — all with per-source status and zero cross-request contamination.

## 3. Cross-cutting invariants (bind all 6 sub-projects)

1. **Context engineering under concurrency.** Per-request state lives ONLY in: (a) LangGraph state keyed by `thread_id`, (b) the `current_run_id` `ContextVar`, (c) DB rows keyed by `run_id`/`session_id`. **No module-level mutable per-request state.** Concurrency = one asyncio task per run + `Semaphore(max_concurrent_runs)` + per-session mutex. (Verified sound in the current code; every new component must preserve it.)
2. **Agent identity.** Named agents (WebSearch / Tagging / Dashboard) each emit a `*_started` / `*_finished` status (count, elapsed, one-line summary) and write a typed **HandoffEnvelope** to state for the next agent. (Full treatment in sub-project #4; this sub-project introduces the WebSearch Agent's status + envelope.)

## 4. Components

### 4.1 Competitor resolution — `app/agents/competitor_agent.py`
`async resolve_competitors(brand, project_id, override: list[str] | None) -> list[str]` with a fallback ladder:
1. `override` (from an email/Teams instruction like `competitors: Carrier, Daikin`) — wins if present.
2. `Project.competitors` (new column) if non-empty.
3. Mem0 **SEMANTIC** recall (`"<brand> competitors"`) — reuse prior research.
4. Else a `CompetitorResearch` GuardedAgent: web-search the brand's official/industry pages, return the top 5 direct competitors (typed output). Cache the result to `Project.competitors` **and** Mem0 SEMANTIC (this also fixes the "semantic memory is never written" gap from the audit).

Capped at 5. Failure → empty list + a warning in status (never blocks the run).

### 4.2 Query-plan builder — `app/services/query_plan.py`
`build_query_plan(brand, competitors, industry) -> list[query_group]`:
```
[{"name":"Brand News",       "queries":[brand], "subject":brand},
 {"name":"Competitors News", "queries":[c1..c5], "subject_per_query":True},
 {"name":"Industry News",    "queries":[industry terms]}]
```
Each competitor is its **own** query string (Google News is one-keyword-per-request, then unioned — already correct). Industry terms derive from `Project.industry` (the "Industry News" group is skipped when `Project.industry` is empty). `_start_run` calls this instead of the hardcoded single group; the WS query-builder and scheduler paths converge on it too.

### 4.3 Sources Orchestrator (the WebSearch Agent) — refactor `ingestion_service.collect()`
- Replace the nested sequential loop with **concurrent fan-out**: build the full `(connector, query, subject)` task list, run under `asyncio.Semaphore(SOURCE_CONCURRENCY)` (default 8), gather.
- **Capabilities-aware:** pass source-side filters (date/lang/country/count) only where the connector declares support.
- **Per-source isolation:** each task wrapped so one source's failure/timeout becomes an entry in its status, never aborts the run.
- **Per-source status events:** `source_started` / `source_finished {source, query_group, count, elapsed_ms, errors}` on the event bus, plus a WebSearch-Agent `agent_started` / `agent_finished {total, per_source, elapsed, summary}`.
- After fan-out: existing `dedupe()` → `relevancy_filter()` → write `source_file` → **HandoffEnvelope** → Gate 1.

### 4.4 Google News decode — `app/tools/scraping/gnews.py`
`decode(url) -> (real_url, publisher_domain) | None`: parse the `news.google.com/rss/articles/CBMi…` base64 payload; fallback to a lightweight redirect-follow. `publisher_name` comes from the RSS `<source>` title. Applied inside google_news normalization so **real** publisher/domain flow to every downstream stage. Decode failure → keep original URL + `<source>` name (best-effort, never blocks).

### 4.5 Per-subject attribution
Add `subject_brand: str` to `RawArticle` (the brand/competitor the query targeted). The orchestrator stamps it from the query group. Tagging and the chart builders (SOV, impact, competitive matrix — sub-project #2) attribute by `subject_brand` **and** extracted entities, so each competitor gets its true share instead of collapsing to the primary brand.

### 4.6 apify / xpoz
Documented deferral: remain optional stubs. The orchestrator already treats sources as optional. **Open question for the user:** provide an Apify token + actor id (and clarify what "XPOz" is / its API) and they become first-class; otherwise they stay out of scope for #1.

## 5. Data flow

`"start BeOne"` → `resolve_competitors` (brand + 5) → `build_query_plan` (Brand + Competitors + Industry groups) → **WebSearch Agent** concurrent fan-out over all enabled sources × all queries → gnews decode + per-connector normalize (+ `subject_brand`) → merge → dedupe → relevancy → `source_file` → HandoffEnvelope → Gate 1 (collected CSV, now with real publishers, countries, and per-competitor attribution).

## 6. Error handling

- Per-source failure → isolated, recorded in `source_finished.errors`, run continues.
- Competitor research failure → brand-only + status warning.
- gnews decode failure → original URL + `<source>` publisher.
- All bounded by the source semaphore + per-request timeouts; concurrency capped by `max_concurrent_runs`.

## 7. Testing

- **Unit:** gnews `decode` (fixture URLs incl. malformed), `build_query_plan`, `resolve_competitors` ladder (mock research + memory), orchestrator fan-out (task count, one failing source isolated), dedupe with real publisher domains, `subject_brand` stamping.
- **Integration:** `"start BeOne"` → `source_file` contains brand **and** competitor-attributed articles, **no `news.google.com` domains**, per-source status events present.
- **Concurrency/context test (invariant guard):** launch BeOne **and** Trane runs simultaneously; assert each run's `source_file` contains only its own `subject_brand`s, `current_run_id` attribution is correct per run, and neither run's memory/status events leak into the other.

## 8. Migrations

- Alembic: add `Project.competitors JSONB NOT NULL DEFAULT '[]'`.

## 9. Out of scope (later sub-projects)

Chart rendering/reconciliation (#2), logo/author deep-enrichment (#3), Task-ID subjects + per-agent emails + envelopes surfaced to the user (#4), Vercel-hosted persistent reports (#5), self-heal wiring + memory-loop unification (#6). This sub-project only makes the *data* correct and the *collection* a real concurrent orchestrator.
