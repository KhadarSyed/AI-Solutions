"""Playbook §10 — quantitative models: PR impact, gauge bands, reputation pillars."""

SENTIMENT_MULTIPLIER = {"POS": 1.2, "NEU": 1.0, "NEG": -1.5}
SPOKESPERSON_WEIGHT = {"c_suite": 5, "mid_level": 3, "none": 1}

PILLAR_WEIGHTS = {
    "trust": 0.25,
    "advocacy": 0.20,
    "value_perception": 0.15,
    "social_responsibility": 0.15,
    "brand_strength": 0.15,
    "risk": 0.10,
}

_GAUGE_BANDS = [(0, "Very Poor"), (15, "Poor"), (30, "Good"), (45, "Very Good")]


def pr_impact(article: dict, keyword_weight: float = 1.0) -> float:
    reach_w = article.get("reach_weight", 1) or 1
    spokes_w = SPOKESPERSON_WEIGHT.get(article.get("spokesperson_level", "none"), 1)
    sent_m = SENTIMENT_MULTIPLIER.get(article.get("xai_sentiment", "NEU"), 1.0)
    return round(keyword_weight * reach_w * spokes_w * sent_m, 2)


def gauge_rating(daily_average: float) -> str:
    if daily_average < 0:
        return "Very Poor"
    label = "Excellent"
    for threshold, name in reversed(_GAUGE_BANDS):
        if daily_average <= threshold:
            label = name
    return label


def _ratio(part: int, total: int) -> float:
    return part / total if total else 0.0


def reputation_pillars(articles: list[dict]) -> dict:
    """Six pillars, each 0–100, weighted composite per playbook."""
    total = len(articles)
    if not total:
        return {"pillars": {}, "composite": 0.0}

    pos = [a for a in articles if a.get("xai_sentiment") == "POS"]
    neg = [a for a in articles if a.get("xai_sentiment") == "NEG"]
    crisis = [a for a in neg if a.get("priority_watch")]
    tier1 = [a for a in articles if a.get("tier1")]
    spokes = [a for a in articles if a.get("spokesperson_level", "none") != "none"]

    def theme_share(*needles: str) -> float:
        hits = [
            a for a in articles
            if any(n in (a.get("xai_theme", "") + " " + a.get("xai_section", "")).lower()
                   for n in needles)
        ]
        return _ratio(len(hits), total)

    sentiment_score = _ratio(len(pos), total)
    prominence = _ratio(len(spokes), total)
    tier1_share = _ratio(len(tier1), total)

    brand_mentions = sum(
        len((a.get("entities") or {}).get("brand_of_interest", [])) for a in articles
    )
    rival_mentions = sum(
        len((a.get("entities") or {}).get("competitors", [])) for a in articles
    )
    sov = _ratio(brand_mentions, brand_mentions + rival_mentions)

    pillars = {
        "trust": 100 * (0.4 * sentiment_score + 0.3 * prominence + 0.3 * tier1_share),
        "advocacy": 100 * (0.6 * _ratio(len(pos), total) + 0.4 * prominence),
        "value_perception": 100 * theme_share("product", "service", "value", "price"),
        "social_responsibility": 100 * theme_share("esg", "csr", "sustain", "communit"),
        "brand_strength": 100 * sov,
        "risk": 100 * (1 - _ratio(len(neg) + len(crisis), total)),
    }
    composite = sum(pillars[k] * w for k, w in PILLAR_WEIGHTS.items())
    return {
        "pillars": {k: round(v, 1) for k, v in pillars.items()},
        "composite": round(composite, 1),
        "share_of_voice": round(sov, 3),
    }
