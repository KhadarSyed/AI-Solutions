"""Dashboard 1 — Media Monitoring: section-grouped feed, priority + reach ordered."""

from collections import defaultdict


def build(articles: list[dict], sections: list[str]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        grouped[a.get("xai_section") or "Unsectioned"].append(a)

    ordered_sections = [s for s in sections if s in grouped] + [
        s for s in grouped if s not in sections
    ]
    feed = []
    for section in ordered_sections:
        items = sorted(
            grouped[section],
            key=lambda a: (not a.get("priority_watch", False), -(a.get("monthly_reach") or 0)),
        )
        feed.append({
            "section": section,
            "count": len(items),
            "articles": [
                {
                    "id": a["id"], "title": a.get("title", ""),
                    "publisher": a.get("publisher_name", ""),
                    "domain": a.get("publisher_domain", ""),
                    "date": a.get("published_date"), "url": a.get("url", ""),
                    "sentiment": a.get("xai_sentiment"), "theme": a.get("xai_theme"),
                    "priority_watch": a.get("priority_watch", False),
                    "reach": a.get("monthly_reach", 0),
                }
                for a in items
            ],
        })
    priority_count = sum(1 for a in articles if a.get("priority_watch"))
    return {"kind": "media_monitoring", "sections": feed,
            "kpis": {"total": len(articles), "priority_watch": priority_count}}
