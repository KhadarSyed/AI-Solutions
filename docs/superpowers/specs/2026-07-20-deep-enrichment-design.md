# Sub-project 2 — Deep Enrichment (official-site logo, author, country, real URLs) + brand banner

**Status:** proposed · **Date:** 2026-07-20 · **Part of:** the 6-sub-project program (this is #2; precedes #3 dashboard which consumes these assets).

## Problem (from the live audit + validation)
- **Brand logo shows a monogram "B"** — `logos.py` only fetches the Google S2 **favicon**, never the real logo, and falls back to a letter badge.
- **Author ≈ 0%** — most sources carry no byline; article pages aren't reliably fetched (no `curl_cffi`, so Scrapling can't pass bot-walls).
- **~15% of articles still show `news.google.com`** — the opaque Google-News URLs weren't resolved in #1.
- **Hero banner** should carry a **brand-relevant Pexels video** (key present, resolver missing).

## Goal
A brand/article **enrichment layer** that fills real assets/data via Scrapling → browser-mcp, time-boxed and best-effort, feeding the #3 dashboard:
1. Real **official-site logo** (not favicon), base64-embedded.
2. **Author** bylines from the real article page.
3. **Country** (keep #1's LLM-knowledge fill; add about/contact page extraction for gaps).
4. **Real publisher URL** for the remaining `news.google.com` articles.
5. **Pexels brand video** URL for the banner.

## Cross-cutting (from program overview)
Context-isolation invariants hold; enrichment is best-effort (never blocks a gate); time-boxed with a per-run budget; learned facts cached to RESEARCH memory.

## Components

### 2.1 Real logo resolver — rewrite `app/tools/enrichment/logos.py`
New ladder per brand/competitor name:
1. RESEARCH memory (learned logo data-URI) — reuse.
2. Resolve official domain (existing `_find_domain` via DDG; skip social/wiki).
3. **Fetch the homepage** (`fetcher.fetch_html`, Scrapling fallback) and extract the best logo via `_extract_logo_url(html, base_url)`, preferring in order: `<meta property="og:image">` → `<link rel="apple-touch-icon">` (largest) → `<link rel="icon" sizes>` (largest) → a header `<img>` whose class/id/alt/src contains "logo" → the SVG logo if inlined.
4. Download the chosen asset, validate it is an image (content-type starts `image/`, 512 B–1.5 MB), base64 data-URI it.
5. Fallbacks: the 128px Google favicon (today's behavior), then a monogram.
Cache the resolved data-URI to RESEARCH memory (`"<name> → logo <data-uri-hash>"` + metadata). Same path resolves competitor logos.

### 2.2 Pexels brand video — new `app/tools/enrichment/pexels.py`
`async brand_video(brand: str, industry: str | None) -> dict | None` → GET `https://api.pexels.com/videos/search?query=<brand-or-industry>&orientation=landscape&size=medium&per_page=5` with `Authorization: <PEXELS_API_KEY>`; pick a landscape clip, return `{video_url, poster, author}`. Enabled by `settings.pexels_api_key`. Cached to RESEARCH memory. #3 renders it (remote-streamed with a poster fallback).

### 2.3 Author byline enrichment — extend `source_enricher`
For articles still missing `author` (budgeted, e.g. ≤25 pages, ~2-min window): fetch the **real** article URL (after 2.4 resolves gnews) via `fetcher.fetch_html` (Scrapling) → browser-mcp fallback; extract via `extractor` — `<meta name="author">`, JSON-LD `author.name`, and `By …/Written by/Posted by` patterns. Concurrency-capped; failures skipped.

### 2.4 Real Google-News URL resolution
For `publisher_domain == "news.google.com"` articles: resolve the real URL best-effort — try `gnews.resolve_via_redirect`, then (if `browser_mcp.enabled()`) `browser_mcp.navigate_and_read` to capture the final URL. On success, rewrite `url` + `publisher_domain` (+ publisher_name from the page). Budgeted; unresolved keep the RSS `<source>` name.

### 2.5 Dependency: `curl_cffi`
Add `curl_cffi` to `pyproject.toml` deps and install in `docker/api.Dockerfile` so Scrapling can impersonate a browser TLS fingerprint and fetch past 403 bot-walls (the current `No module named 'curl_cffi'` failures).

## Error handling
Every fetch/extract wrapped; failures degrade to the next ladder rung (logo→favicon→monogram; author→blank; url→keep gnews). Nothing blocks collection or a gate. All external calls time-boxed.

## Testing
- **Unit:** `_extract_logo_url` (fixture HTML → prefers og:image/apple-touch-icon over favicon; header-img-logo fallback), image validation (rejects non-image/oversize), Pexels response parsing (fixture JSON → video_url), byline extraction (fixture HTML → author), gnews redirect resolution (mocked).
- **Integration (container, real network):** resolve BeOne logo → a real `data:image/...` (not monogram); Pexels returns a video URL; a sample of gnews URLs resolve to real domains; author fill-rate > 0 on a real collection.

## Out of scope (→ #3)
Rendering the logo/banner/insights in the dashboard, chart fixes, Apple-50/50 restyle.
