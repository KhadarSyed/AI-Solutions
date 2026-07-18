"""File upload path — CSV/XLSX/JSON → normalized RawArticle records.

Header names are matched loosely (case/space/underscore-insensitive aliases)."""

import io
import json
from datetime import date

import pandas as pd

from app.tools.connectors.base import RawArticle

_ALIASES: dict[str, list[str]] = {
    "publisher_name": ["publisher", "publisher name", "source", "outlet", "publication"],
    "title": ["title", "headline", "article title"],
    "content": ["content", "body", "text", "article", "summary", "description"],
    "publisher_domain": ["domain", "publisher domain", "site", "website"],
    "published_date": ["date", "published", "published date", "pub date", "publish date"],
    "url": ["url", "link", "article url", "href"],
    "author": ["author", "byline", "journalist", "written by", "writer"],
    "country": ["country", "region", "market"],
    "language": ["language", "lang"],
}


def _canonical(col: str) -> str | None:
    key = col.strip().lower().replace("_", " ")
    for canon, aliases in _ALIASES.items():
        if key == canon.replace("_", " ") or key in aliases:
            return canon
    return None


def _coerce_date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts.date()


def parse_upload(filename: str, data: bytes) -> list[RawArticle]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data))
    elif lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data))
    elif lower.endswith(".json"):
        payload = json.loads(data.decode("utf-8"))
        records = payload if isinstance(payload, list) else payload.get("articles", [])
        df = pd.DataFrame(records)
    else:
        raise ValueError(f"unsupported upload type: {filename} (use CSV, XLSX or JSON)")

    rename = {}
    for col in df.columns:
        canon = _canonical(str(col))
        if canon and canon not in rename.values():
            rename[col] = canon
    df = df.rename(columns=rename)

    if "title" not in df.columns or "url" not in df.columns:
        raise ValueError("upload must contain at least title and url columns")

    articles: list[RawArticle] = []
    for _, row in df.iterrows():
        def val(col: str, default: str = "", _row=row) -> str:
            v = _row.get(col)
            return default if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

        if not val("title") or not val("url"):
            continue
        articles.append(
            RawArticle(
                publisher_name=val("publisher_name"),
                title=val("title"),
                content=val("content"),
                publisher_domain=val("publisher_domain"),
                published_date=_coerce_date(row.get("published_date")),
                url=val("url"),
                author=val("author"),
                country=val("country", "all") or "all",
                language=val("language"),
                source="file_upload",
            )
        )
    return articles
