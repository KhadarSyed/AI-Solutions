"""Gate CSV exports — the file that travels to the user's channel at each gate.

The tagged CSV is also the monitoring opt-out surface: the user-facing `Monitoring`
column (TRUE/FALSE) maps to `is_approved_for_monitoring`; editing a row to FALSE and
replying with the CSV drops it from the final dashboard (see review_service.apply_monitoring_csv)."""

import csv
import io

COLLECTED_COLUMNS = [
    "publisher_name", "title", "content", "publisher_domain", "published_date",
    "published_time", "url", "author", "country", "language", "source", "query_group",
]

TAGGED_COLUMNS = COLLECTED_COLUMNS[:-1] + [
    "id", "xai_sentiment", "sentiment_confidence", "xai_sentiment_reason",
    "theme_primary", "theme_secondary", "theme_tertiary", "theme_confidence",
    "xai_theme_reason", "emotions", "emotion_confidence", "signals", "signal_confidence",
    "xai_section", "section_confidence", "priority_watch", "spokesperson_level",
    "entity_brand", "entity_competitors", "entity_organizations", "entity_people",
    "entity_products", "relevancy_confidence", "xai_relevancy_reason", "monthly_reach",
    "is_approved", "Monitoring",
]

# columns whose value is a list joined with "; "
_LIST_COLUMNS = frozenset({"emotions", "signals"})
# CSV column -> entities sub-key(s) it flattens
_ENTITY_MAP = {
    "entity_brand": ("brand_of_interest",),
    "entity_competitors": ("competitors", "other_competitors"),
    "entity_organizations": ("organizations",),
    "entity_people": ("peoples",),
    "entity_products": ("products",),
}


def _bool_str(v) -> str:
    return "TRUE" if v else "FALSE"


def _join(values) -> str:
    if isinstance(values, str):
        return values
    return "; ".join(str(x) for x in (values or []) if str(x).strip())


def _cell(article: dict, column: str) -> str:
    if column == "Monitoring":
        return _bool_str(article.get("is_approved_for_monitoring"))
    if column == "is_approved":
        return _bool_str(article.get("is_approved"))
    if column in _ENTITY_MAP:
        entities = article.get("entities") or {}
        vals: list = []
        for key in _ENTITY_MAP[column]:
            vals += entities.get(key, []) or []
        return _join(vals)
    if column in _LIST_COLUMNS:
        return _join(article.get(column))
    value = article.get(column, "")
    if column == "content" and isinstance(value, str):
        return value[:1500]
    return "" if value is None else str(value) if not isinstance(value, str) else value


def articles_to_csv(articles: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for a in articles:
        writer.writerow({c: _cell(a, c) for c in columns})
    return buf.getvalue().encode("utf-8-sig")   # BOM: opens cleanly in Excel
