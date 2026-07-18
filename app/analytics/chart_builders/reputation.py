"""Dashboard 5 — Reputation Index: six weighted pillars, radar/waterfall data,
sentiment×section heatmap."""

from collections import defaultdict

from app.analytics.scoring import PILLAR_WEIGHTS, reputation_pillars


def build(articles: list[dict]) -> dict:
    rep = reputation_pillars(articles)

    heatmap: dict[str, dict[str, int]] = defaultdict(lambda: {"POS": 0, "NEU": 0, "NEG": 0})
    for a in articles:
        heatmap[a.get("xai_section") or "Unsectioned"][a.get("xai_sentiment", "NEU")] += 1

    waterfall = [
        {"pillar": name, "score": rep["pillars"].get(name, 0.0), "weight": weight,
         "contribution": round(rep["pillars"].get(name, 0.0) * weight, 1)}
        for name, weight in PILLAR_WEIGHTS.items()
    ]
    return {
        "kind": "reputation_index",
        "composite": rep.get("composite", 0.0),
        "radar": rep.get("pillars", {}),
        "waterfall": waterfall,
        "heatmap": {k: dict(v) for k, v in heatmap.items()},
        "share_of_voice": rep.get("share_of_voice", 0.0),
    }
