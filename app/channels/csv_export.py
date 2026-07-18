"""Gate CSV exports — the file that travels to the user's channel at each gate."""

import csv
import io

COLLECTED_COLUMNS = [
    "publisher_name", "title", "content", "publisher_domain", "published_date",
    "url", "author", "country", "language", "source", "query_group",
]

TAGGED_COLUMNS = COLLECTED_COLUMNS[:-1] + [
    "id", "xai_sentiment", "xai_theme", "xai_section", "priority_watch",
    "sentiment_confidence", "theme_confidence", "section_confidence",
    "relevancy_confidence", "xai_sentiment_reason", "xai_theme_reason",
    "xai_relevancy_reason", "monthly_reach", "is_approved", "is_approved_for_monitoring",
]


def articles_to_csv(articles: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for a in articles:
        row = {c: a.get(c, "") for c in columns}
        if "content" in row and isinstance(row["content"], str):
            row["content"] = row["content"][:1500]
        writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")   # BOM: opens cleanly in Excel
