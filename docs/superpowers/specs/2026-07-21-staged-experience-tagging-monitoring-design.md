# Staged experience + rich tagging + monitoring opt-out

**Date:** 2026-07-21
**Status:** Approved
**Sequence:** Implement SECOND (after the embedding-independent retrieval fix).

## Problem / intent

The pipeline's HITL gates send plain text and a CSV; tagging under-captures; the final
dashboard isn't gated by an explicit per-article monitoring decision. The user wants:

1. A **richer tagging schema** (theme tiers, emotions, signals, product entities, split
   date/time).
2. A **monitoring opt-out** so only chosen items reach the final dashboard/link.
3. **CSS-styled staged updates** where each stage narrates itself with charts, tables,
   and two-line summaries.

Anti-hallucination constraint applies throughout: every displayed number is computed
deterministically; controlled vocabularies prevent the tagger from inventing categories.

## Design

### A. Tagging schema — `app/tools/schemas/tagging.py`

Extend `TaggedArticle`:

- Themes: `theme_primary`, `theme_secondary`, `theme_tertiary`. Keep `xai_theme =
  theme_primary` so existing charts/CSV keep working.
- `emotions: list[str]` and `signals: list[str]`, **both from controlled vocabularies
  enforced by Pydantic validators** — the model cannot invent a category (anti-hallucination),
  and values aggregate cleanly.
  - Emotions (Plutchik): joy, trust, fear, surprise, sadness, disgust, anger, anticipation.
  - Signals (PR): product_launch, partnership, regulatory, crisis, earnings,
    leadership_change, expansion, award, lawsuit, recall, hiring, research, other.
- `ArticleEntities.products: list[str]` (Brand / Competitors / Org / People / Product all present).
- Add `emotion_confidence: float`, `signal_confidence: float` (ge=0 le=1). Reasons stay mandatory.
- System prompt updated to instruct all new fields + the controlled vocabularies.

### B. Published date + time split

- `RawArticle`: keep/derive an internal `published_at` datetime (source of truth). Emit
  two fields: `published_date` (YYYY-MM-DD) and `published_time` (HH:MM). When a source
  gives only a date, `published_time` is **blank — never fabricated**.
- Connectors preserve time when the source provides it; normalization fills the two fields.
- Dashboard exposes date **and** time filter controls; volume-over-time uses `published_at`.

### C. CSV — `app/channels/csv_export.py`

- Split `published_date` → `published_date` + `published_time` in both column sets.
- `TAGGED_COLUMNS` adds: `theme_primary`, `theme_secondary`, `theme_tertiary`, `emotions`,
  `signals`, `products`.
- Add a **`Monitoring`** column = `is_approved_for_monitoring` (default TRUE). This is the
  column the user edits to FALSE to drop items. List columns serialize as `; `-joined.

### D. Monitoring opt-out round-trip — inbound handler + Gate 2

- All tagged rows default `is_approved_for_monitoring=TRUE`.
- Gate-2 tagged CSV carries `Monitoring=TRUE` for all rows.
- On the Gate-2 approval reply: if a **CSV is attached**, parse it; any row whose
  `Monitoring` ∈ {FALSE, 0, no, off, ""} → set that article's flag FALSE via a
  `SELECT … FOR UPDATE` read-modify-write on the `tagged_file` JSONB (edit + correction
  log + cache invalidation + embedding flag sync, in one transaction — the existing review
  mutation pattern). Rows omitted or TRUE stay TRUE.
- Reply "approved" with **no CSV** → all stay TRUE.
- Dashboard + final delivered link are built from `is_approved_for_monitoring=TRUE` only.

### E. Staged CSS-styled updates — `app/channels/stage_report.py` (new) + notifier

Each stage message uses the Apple×editorial theme; header = **`[TASK-ID] {brand}
Monitoring` · Stage N / X · <planned|processed>**.

- **Stage 1 (collection):** "Found competitors: …"; counts **per brand / per competitor /
  per industry**; CSS `<div>` bar charts + two-line summary; collected **CSV attached**;
  `stage1_summary.html` (real ECharts) attached. On approval → short reply: "Approved —
  tagging N articles across …".
- **Stage 2 (tagging):** # enriched; **top-5 themes / top-5 publications / top-5 authors /
  top-3 volume-over-time peaks**; CSS tables + bar charts + two-line summaries; tagged
  **CSV attached** (with `Monitoring` column); `stage2_summary.html` (ECharts) attached.
- **Final:** dashboard created; **# approved vs # dropped**; live link; clean streaming.

`OutboundMessage` gains an optional `html` field. The email adapter sends multipart
(`text` + `html`); Teams gets a CSS-lite / markdown fallback. Charts in the body are
CSS/`<div>` bars (render inline everywhere); real ECharts ride along as the
`stageN_summary.html` attachment (email can't run JS inline).

### F. Deterministic aggregation + summaries — `app/analytics/stage_stats.py` (new)

Pure functions compute every displayed number: Stage-1 counts per brand/competitor/
industry; Stage-2 top-5 themes/publications/authors, top-3 volume peaks, enriched count;
final approved-vs-dropped. **Two-line summaries are templated directly from these numbers**
— deterministic, no LLM invention. (Any optional LLM polish sees only the computed numbers.)
These feed the CSS charts, the ECharts snapshots, and the summaries from one source.

### G. Dashboard — `app/services/html_renderer.py` + builders

Consume the new fields (theme tiers, emotions, signals, products), show `published_date`
and `published_time` columns with a **date & time filter**, and read
`is_approved_for_monitoring=TRUE` rows only.

## Testing

- Controlled-vocab validators reject an invented emotion/signal.
- Opt-out CSV drops only rows marked FALSE; omitted rows stay TRUE; no-CSV approve keeps all.
- `stage_stats` aggregations exact on a fixed corpus (counts, top-N, peaks, approved/dropped).
- Staged HTML is self-contained and renders; ECharts snapshot opens standalone.
- Numeric summaries are provably templated (no LLM path for the numbers).
- Date/time split preserves time when present and leaves time blank (not fabricated) otherwise.

## Rollback

Schema additions are additive (new optional fields default empty; `xai_theme` preserved).
Monitoring defaults TRUE so the flow is unchanged if the user never edits the CSV. Staged
updates fall back to the existing plain-text notifier if `html` is absent.
