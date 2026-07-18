"""Dashboard 4 — Narrative Intelligence: deterministic thread base (theme
clusters with representatives); the LLM layer adds storyboards on top."""

from collections import defaultdict


def build(articles: list[dict]) -> dict:
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        theme = a.get("xai_theme") or "Untagged"
        by_theme[theme].append(a)

    threads = []
    for theme, items in sorted(by_theme.items(), key=lambda kv: -len(kv[1]))[:10]:
        dates = sorted(str(a.get("published_date"))[:10] for a in items
                       if a.get("published_date"))
        sentiments = defaultdict(int)
        for a in items:
            sentiments[a.get("xai_sentiment", "NEU")] += 1
        threads.append({
            "theme": theme,
            "count": len(items),
            "first_seen": dates[0] if dates else None,
            "last_seen": dates[-1] if dates else None,
            "sentiment": dict(sentiments),
            "representatives": [
                {"id": a["id"], "title": a.get("title", ""),
                 "publisher": a.get("publisher_name", "")}
                for a in sorted(items, key=lambda x: -(x.get("monthly_reach") or 0))[:3]
            ],
        })
    return {"kind": "narrative_intelligence", "threads": threads}
