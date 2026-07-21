# Staged conversation v2 — plan-first gates, in-thread replies, local embeddings, pro emails

**Date:** 2026-07-21
**Status:** Approved (3 gates; local ONNX embeddings; industry-standard interactive emails)

## Goal

Turn the channel conversation into a clear, interactive, professional experience. Every
agent step replies IN-THREAD to the user's trigger email (never a new thread), on the
origin channel (email / Teams chat / Teams channel). Restore semantic recall with a local
embedding model (no Azure dependency). Make the emails feel like one product with the
dashboard, and never leave the user unsure what to do next.

## Gate structure (3 gates)

Graph: `START → plan_gate → collect → enrich → collect_gate → tag → tagged_gate →
dashboards → reflect → deliver → END`

1. **plan_gate (NEW, pre-collection):** brands + logos, duration (today−N → today),
   intent + points, goal, and the Boolean queries (from `query_groups`). Approve / change.
   Change → apply (competitors, queries, duration) + resend full plan + re-interrupt.
   Approve → "Stage 1 Approved — WebSearch started for …".
2. **collect_gate (collection KPIs + tagging plan, merged):** total articles, country
   count, top-5 publications, top-5 authors (table) + the tagging plan (brands, sources,
   enrichment points). Approve, or add data points. Adding → persist to Mem0 (procedural,
   project-scoped) AND apply to this run's tagging prompt.
3. **tagged_gate (sign-off):** tagged CSV + KPI body (tagged, dropped, memory updates) +
   charts Top-5 themes, Top-5 signals, SOV per brand, Sentiment per brand, each with a
   two-line summary. Monitoring opt-out (edited CSV) still honored here.

`deliver`: final email = home-page HTML snapshot (KPIs + headline charts, email-safe) +
"View full report in browser" Vercel link (Daily Monitoring + Media Monitoring).

## Interactivity / clarity (industry-standard email UX)

- **Stage stepper** in every email header: `● Plan → ○ Collect → ○ Tag → ○ Report`.
- **One clear CTA box** per gate stating exactly what to reply. Natural-language intent
  parsing: approve/yes/go ahead; change: …; add: … (not rigid keywords).
- **Confirmation echoes:** the next email restates what was understood and applied.
- **Self-contained header:** brand · Task-ID · date/time on every message.
- **Expectation-setting:** rough duration; gate stays open until the user replies.
- **Takeaway-first layout:** KPI cards on top, then charts with plain-language captions.
- **Graceful failure email:** non-technical note + next step, in-thread.
- **Email standards:** 640px single-column responsive, inline CSS, system/web-safe fonts,
  remote logo `<img>` with `alt` (data-URIs get stripped in email) + monogram fallback,
  accessible contrast, tappable anchor buttons, preheader line, Apple×editorial theme.

## In-thread replies (fix + verify)

`origin_address` carries the trigger's Graph `message_id` (from `get_pending_mentions`);
the adapter uses `mail_reply` when present. Guarantee every run notification replies to the
trigger (same `conversationId`), confirm `message_id` is non-empty end-to-end, and verify
by reading the delivered thread back (conversationId match). Same for Teams chat/channel.

## Local embeddings

Add `fastembed` (BAAI **bge-small-en-v1.5**, 384-dim, ONNX/CPU) bundled in the API image.
`EMBEDDING_BACKEND=local|azure` (default local). Migration alters
`article_embeddings.embedding` to `vector(384)` (safe — table is empty today).
`embeddings_available()` becomes backend-aware; tagging re-embeds via the local model →
vector recall works, lexical fallback still beneath it, relevance floor intact (no
hallucination regression). Azure auto-resumes if `EMBEDDING_BACKEND=azure` and the
deployment exists.

## Tagging-memory updates

`collect_gate` feedback that adds a data point → parsed (heuristic + small guarded agent),
stored to Mem0 (procedural, project-scoped, inherited by future runs) and injected into
this run's tagging prompt via run state.

## Components

- `app/orchestration/graphs/pipeline_graph.py`: new `plan_gate`; merge tagging-plan into
  `collect_gate`; richer `tagged_gate`; `deliver` snapshot.
- `app/channels/stage_report.py`: `plan_html`, `collection_kpi_html`, `tagged_results_html`,
  `home_snapshot_html`, stepper + CTA + logo helpers.
- `app/analytics/stage_stats.py`: country count, top authors, per-brand SOV + sentiment,
  dropped/memory-update counts, plan summary.
- `app/channels/teams_mcp.py`: guarantee in-thread reply; HTML already wired.
- `app/llm_gateway/embeddings.py` + `app/memory/vector_store.py` + `app/retrieval/bi_encoder.py`:
  local backend; Alembic migration for vector(384).
- `app/agents/tagging_service.py` + memory: run-scoped + persisted tagging additions.
- Reply intent parsing in `app/agents/email_agent.py` (natural language approve/change/add).

## Testing

- Unit: stage_stats new aggregations; stage_report renders stepper/CTA/logos; reply-intent
  parser (approve/change/add variants); local embedder returns 384-dim; monitoring opt-out
  unaffected.
- Live: BeOne run through all 3 gates via real email; read thread back (conversationId
  match proves in-thread); confirm local vector recall + grounded chat; final snapshot email.

## Rollback

`EMBEDDING_BACKEND=azure` + revert migration restores prior embeddings. Gate additions are
additive nodes; the monitoring opt-out and retrieval floor are unchanged.
