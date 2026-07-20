# PR Intelligence — "100% best-practice agentic" program overview

Captures the full target design agreed 2026-07-20. Each sub-project gets its own detailed spec → implementation plan → build, in order. This doc is the index + the cross-cutting decisions of record.

## Cross-cutting decisions (bind every sub-project)
- **Context engineering under concurrency (verified sound, must stay so):** per-request state lives only in LangGraph state (by `thread_id`), the `current_run_id` ContextVar, or DB rows (by `run_id`/`session_id`). No module-level mutable per-request state. One asyncio task per run + `Semaphore(max_concurrent_runs)` + per-session mutex. A concurrency test (two brands at once → zero cross-contamination) guards this.
- **Agent identity:** named agents (WebSearch / Tagging / Dashboard / Chat) each emit `*_started` / `*_finished` status (count, elapsed, 1-line summary) and pass a typed **HandoffEnvelope** to the next.
- **Competitors on by default** (5), unless the user explicitly names competitors.
- **Email subject gating:** act only on `start <brand>…` (from a registered stakeholder) or a reply carrying the Task-ID tag `[BRAND-YYYYMMDD-NNN]`; ignore all other mail.
- **Report styling:** 50% Apple (whitespace, restrained palette + one accent/section, soft-shadow rounded cards, crisp type scale, smooth micro-interactions) / 50% editorial (voice, marble+Pexels hero, per-section gradients).
- **Delivery:** CSV inline + dashboard/report via OneDrive link (never `.html` attachment) + local `reports/` export; email is delayed (Check Point) so "sent" ≠ instant.

## Sub-projects (build order)
1. **WebSearch + Sources Orchestrator** — competitors-by-default; concurrent capabilities-aware fan-out over Google-News + DuckDuckGo + SearXNG + SerpAPI + Tavily + **Apify** + **XPOz**; decode Google-News → real publisher/domain; per-`subject_brand` attribution; subject gating. *(Detailed spec written.)*
2. **Deep enrichment** (browser-mcp + Scrapling) — real official-site **logo** (og:image/logo, not favicon), **author** bylines from article page, **country** from about/contact; install `curl_cffi`.
3. **Dashboard redesign** — Apple-50/50; multi-view **Home → Daily Monitoring → Media Measurement (sub-tabs: Overview/Sentiment/Themes/Media Coverage/Key Stories) → Narrative Intelligence → PR Impact → Reputation Index**; **two-line grounded insight under every chart**; **Pexels brand-related video** hero; fixes: PR-Impact publisher labels (not A1/A2), geo interactive tooltip w/ counts, competitor SOV + competitive matrix, all charts reconciled to fetched/tagged totals.
4. **Multi-agent shell** — Task-ID subjects on every message, per-agent start/finish status emails, handoff envelopes, concurrency made observable, interactive in-thread Q&A.
5. **Embedded agentic chat** — in-dashboard "Ask the data agent"; Brain routing: **RAG over this report's corpus** (pgvector→rerank→cited) | **LLM** (general) | **WEB** (browser-mcp + Scrapling for anything outside); responses timestamped, CSS-formatted (typography + styled tables), can render **ECharts** via the Dashboard Agent; **3-day interaction window** from `generated_at`, enforced server-side.
6. **Persistent hosted reports (Vercel + deployed API)** — dashboards on Vercel; a deployed slim API/RAG service (corpus access) powers the chat so view+chat work from any device within 3 days; durable link per Task ID + a previous-reports index per project.
7. **Close the agentic loops** — wire `self_heal.attempt_heal` into node/executor failure handling (known-fix → web-search → browser ladder); connect tagging bias to Mem0 FEEDBACK (unify the two learning loops); populate or remove the dead SEMANTIC recall.

## Open items (need input, non-blocking for most of #1)
- **XPOz API contract** — base URL, auth header, sample request/response (or docs). Until provided, XPOz stays a stub; the other 6 sources ship in #1.
- **Apify actor id** — preferred news actor? Default: a Google-News / news-search actor (confirm).

## Status
- Audits complete (sources, competitors, memory, dashboard) — findings drove this plan.
- Verdict: strong agentic *prototype* (~70%); this program closes the gaps to best-practice.
