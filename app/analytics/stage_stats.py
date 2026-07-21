"""Deterministic stage aggregations — every number shown in a staged update or its
ECharts snapshot is computed here from the article records. The two-line summaries are
templated straight from these counts (no LLM), so a staged message can never contain a
number the data doesn't support."""

from collections import Counter


def _top(counter: Counter, n: int) -> list[tuple[str, int]]:
    return [(k, v) for k, v in counter.most_common(n) if k]


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def collection_stats(articles: list[dict], *, brand: str, competitors: list[str]) -> dict:
    """Stage 1 — coverage found per brand / competitor / industry."""
    total = len(articles)
    by_group = Counter(a.get("query_group", "") for a in articles)
    by_subject = Counter((a.get("subject_brand") or "").strip() for a in articles)

    brand_n = by_group.get("Brand News", 0)
    comp_n = by_group.get("Competitors News", 0)
    ind_n = by_group.get("Industry News", 0)

    group_series = [("Brand", brand_n), ("Competitors", comp_n), ("Industry", ind_n)]
    subject_series = [(c, by_subject.get(c, 0)) for c in ([brand, *competitors])]
    subject_series = [(k, v) for k, v in subject_series if v] or [(brand, brand_n)]

    comp_list = ", ".join(competitors) if competitors else "none"
    line1 = (f"Collected {total} articles — {brand_n} on {brand} "
             f"({_pct(brand_n, total)}%), {comp_n} across {len(competitors)} competitors, "
             f"{ind_n} on the wider industry.")
    line2 = f"Competitors tracked: {comp_list}."
    return {
        "total": total,
        "group_series": group_series,
        "subject_series": subject_series,
        "competitors": list(competitors),
        "brand": brand,
        "summary": f"{line1}\n{line2}",
    }


def _is_enriched(a: dict) -> bool:
    return bool((a.get("author") or "").strip()) or a.get("country") not in ("", "all", None)


def tagging_stats(articles: list[dict]) -> dict:
    """Stage 2 — what tagging produced across the tagged set."""
    tagged = len(articles)
    enriched = sum(1 for a in articles if _is_enriched(a))

    themes = Counter((a.get("theme_primary") or a.get("xai_theme") or "").strip()
                     for a in articles)
    pubs = Counter((a.get("publisher_name") or a.get("publisher_domain") or "").strip()
                   for a in articles)
    authors = Counter((a.get("author") or "").strip() for a in articles)
    sentiment = Counter(a.get("xai_sentiment", "NEU") for a in articles)

    by_date = Counter(str(a.get("published_date") or "").strip() for a in articles)
    by_date.pop("", None)
    volume_series = sorted(by_date.items())
    volume_peaks = _top(by_date, 3)

    top_themes = _top(themes, 5)
    top_pubs = _top(pubs, 5)
    top_authors = _top(authors, 5)

    theme_txt = ", ".join(f"{t} ({n})" for t, n in top_themes[:3]) or "none yet"
    peak_txt = ", ".join(f"{d} ({n})" for d, n in volume_peaks) or "no dated coverage"
    line1 = (f"Tagged {tagged} articles ({enriched} enriched with author/country); "
             f"sentiment {sentiment.get('POS', 0)}+ / {sentiment.get('NEU', 0)}~ / "
             f"{sentiment.get('NEG', 0)}−.")
    line2 = f"Leading themes: {theme_txt}. Busiest days: {peak_txt}."
    return {
        "tagged": tagged,
        "enriched": enriched,
        "top_themes": top_themes,
        "top_publications": top_pubs,
        "top_authors": top_authors,
        "volume_series": volume_series,
        "volume_peaks": volume_peaks,
        "sentiment": dict(sentiment),
        "summary": f"{line1}\n{line2}",
    }


def final_stats(*, approved_count: int, monitoring_count: int) -> dict:
    """Final — how many reached the dashboard vs were dropped from monitoring."""
    dropped = max(0, approved_count - monitoring_count)
    line1 = (f"Dashboard built from {monitoring_count} monitored articles "
             f"({dropped} excluded of {approved_count} approved).")
    line2 = "Open the live dashboard for the interactive charts, or reply with questions."
    return {
        "in_dashboard": monitoring_count,
        "dropped": dropped,
        "approved": approved_count,
        "summary": f"{line1}\n{line2}",
    }
