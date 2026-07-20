"""Dashboard 3 — PR Impact: score gauge, share of voice, competitive matrix,
outlet tier analysis."""

from collections import Counter, defaultdict

from app.analytics.scoring import gauge_rating, pr_impact


def _attribute(a: dict, brand: str, competitors: list[str]) -> str | None:
    """Attribute an article to the brand or a specific competitor. Prefers the
    subject_brand stamped at collection (query group); falls back to a mention
    match so articles collected before attribution still count."""
    subject = (a.get("subject_brand") or "").strip()
    if subject == brand:
        return brand
    if subject in competitors:
        return subject
    text = (a.get("title", "") + " " + a.get("content", "")).lower()
    if brand.lower() in text:
        return brand
    for c in competitors:
        if c.lower() in text:
            return c
    return None


def build(articles: list[dict], brand: str, competitors: list[str]) -> dict:
    scored = [{**a, "pr_impact": pr_impact(a)} for a in articles]

    days = {str(a.get("published_date"))[:10] for a in articles if a.get("published_date")}
    total_impact = sum(a["pr_impact"] for a in scored)
    daily_avg = round(total_impact / max(len(days), 1), 2)

    # share of voice by collection subject (brand vs each competitor)
    rival_counts: Counter = Counter()
    matrix: dict[str, Counter] = defaultdict(Counter)
    brand_mentions = 0
    for a in articles:
        who = _attribute(a, brand, competitors)
        if who == brand:
            brand_mentions += 1
        elif who in competitors:
            rival_counts[who] += 1
            matrix[who][a.get("xai_sentiment", "NEU")] += 1
    voice_total = brand_mentions + sum(rival_counts.values())

    tiers = Counter(("tier1" if a.get("tier1") else f"w{a.get('reach_weight', 1)}")
                    for a in articles)

    return {
        "kind": "pr_impact",
        "gauge": {"daily_average": daily_avg, "rating": gauge_rating(daily_avg),
                  "total_impact": round(total_impact, 1), "days": len(days) or 1},
        "share_of_voice": {
            "brand": brand, "brand_mentions": brand_mentions,
            "brand_share": round(brand_mentions / voice_total, 3) if voice_total else 0.0,
            "competitors": [
                {"name": c, "mentions": n,
                 "share": round(n / voice_total, 3) if voice_total else 0.0}
                for c, n in rival_counts.most_common()
            ],
        },
        "competitive_matrix": [
            {"competitor": c, **{s: matrix[c].get(s, 0) for s in ("POS", "NEU", "NEG")}}
            for c in competitors if c in matrix
        ],
        "outlet_tiers": dict(tiers),
        "top_impact_articles": sorted(
            ({"id": a["id"], "title": a.get("title", ""),
              "publisher_name": a.get("publisher_name", "") or a.get("publisher_domain", ""),
              "impact": a["pr_impact"], "sentiment": a.get("xai_sentiment")} for a in scored),
            key=lambda x: -abs(x["impact"]),
        )[:10],
    }
