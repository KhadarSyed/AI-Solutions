"""Dashboard 2 — Media Measurement: sentiment split, datewise coverage & reach,
themes, top publications and authors."""

from collections import Counter, defaultdict


def build(articles: list[dict]) -> dict:
    sentiment = Counter(a.get("xai_sentiment", "NEU") for a in articles)

    by_date: dict[str, dict] = defaultdict(lambda: {"count": 0, "reach": 0})
    for a in articles:
        d = str(a.get("published_date") or "unknown")[:10]
        by_date[d]["count"] += 1
        by_date[d]["reach"] += a.get("monthly_reach", 0) or 0
    datewise = [
        {"date": d, **v} for d, v in sorted(by_date.items()) if d != "unknown"
    ]

    themes = Counter(a.get("xai_theme", "") for a in articles if a.get("xai_theme"))
    pubs: dict[str, dict] = defaultdict(lambda: {"count": 0, "reach": 0})
    for a in articles:
        p = a.get("publisher_name") or a.get("publisher_domain") or "unknown"
        pubs[p]["count"] += 1
        pubs[p]["reach"] = max(pubs[p]["reach"], a.get("monthly_reach", 0) or 0)
    authors = Counter(a.get("author", "") for a in articles if a.get("author"))

    total = len(articles) or 1
    return {
        "kind": "media_measurement",
        "kpis": {
            "total": len(articles),
            "positive_pct": round(100 * sentiment.get("POS", 0) / total, 1),
            "negative_pct": round(100 * sentiment.get("NEG", 0) / total, 1),
            "neutral_pct": round(100 * sentiment.get("NEU", 0) / total, 1),
            "total_reach": sum(a.get("monthly_reach", 0) or 0 for a in articles),
        },
        "sentiment_split": dict(sentiment),
        "datewise_coverage": datewise,
        "top_themes": [{"theme": t, "count": c} for t, c in themes.most_common(12)],
        "top_publications": sorted(
            ({"publisher": p, **v} for p, v in pubs.items()),
            key=lambda x: (-x["count"], -x["reach"]),
        )[:12],
        "top_authors": [{"author": a, "count": c} for a, c in authors.most_common(10)],
    }
