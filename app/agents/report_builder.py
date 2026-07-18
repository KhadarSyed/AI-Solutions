"""Stage 7 — branded .docx report from the approved media-monitoring feed.

Reads cached data only (no re-analysis). Brand name picks the template palette
(BeOne default, Trane, Otsuka); an optional date filter narrows the feed."""

import io
from datetime import date

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.observability.logging import get_logger

log = get_logger(__name__)

TEMPLATES = {
    "beone": {"primary": RGBColor(0x1E, 0x40, 0xAF), "accent": RGBColor(0xC2, 0x40, 0x2A),
              "intro": "Media monitoring digest"},
    "trane": {"primary": RGBColor(0xE3, 0x1E, 0x24), "accent": RGBColor(0x1E, 0x4E, 0x96),
              "intro": "Trane media intelligence"},
    "otsuka": {"primary": RGBColor(0x00, 0x3D, 0x7A), "accent": RGBColor(0xC8, 0x9B, 0x3C),
               "intro": "Otsuka coverage report"},
}


def _template_for(brand: str) -> dict:
    b = (brand or "").lower()
    for name, tpl in TEMPLATES.items():
        if name in b:
            return tpl
    return TEMPLATES["beone"]


def _hyperlink_run(paragraph, text: str, color: RGBColor, bold: bool = False):
    run = paragraph.add_run(text)
    run.font.color.rgb = color
    run.bold = bold
    return run


def _in_range(article: dict, date_from: date | None, date_to: date | None) -> bool:
    if not (date_from or date_to):
        return True
    raw = str(article.get("published_date") or "")[:10]
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return date_from is None and date_to is None
    if date_from and d < date_from:
        return False
    return not (date_to and d > date_to)


async def build_report(
    *, session_id: str, brand: str, template: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
) -> bytes:
    store = get_artifact_store()
    charts = await store.get_json(keys.charts_data_file(session_id))
    tagged = await store.get_json(keys.tagged_file(session_id))

    tpl = TEMPLATES.get((template or "").lower()) or _template_for(brand)

    feed = charts.get("dashboards", {}).get("media_monitoring", {}).get("sections", [])
    if not feed:
        # fall back to grouping monitoring-approved articles by section
        from collections import defaultdict

        grouped = defaultdict(list)
        for a in tagged.get("articles", []):
            if a.get("is_approved_for_monitoring") and _in_range(a, date_from, date_to):
                grouped[a.get("xai_section") or "Coverage"].append(a)
        feed = [{"section": s, "articles": items} for s, items in grouped.items()]

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    banner = doc.add_paragraph()
    banner.alignment = WD_ALIGN_PARAGRAPH.CENTER
    br = banner.add_run(f"{brand or 'Brand'} — {tpl['intro']}")
    br.bold = True
    br.font.size = Pt(20)
    br.font.color.rgb = tpl["primary"]

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rng = ""
    if date_from or date_to:
        rng = f" · {date_from or '…'} to {date_to or '…'}"
    meta.add_run(f"Report date: {date.today().isoformat()}{rng}").italic = True

    # section navigation strip — first red, rest blue
    nav = doc.add_paragraph()
    for i, section in enumerate(feed):
        if i:
            nav.add_run("   |   ")
        _hyperlink_run(nav, section["section"],
                       tpl["accent"] if i == 0 else tpl["primary"], bold=True)

    doc.add_paragraph()

    for section in feed:
        articles = [a for a in section.get("articles", []) if _in_range(a, date_from, date_to)]
        if not articles:
            continue
        head = doc.add_heading(section["section"], level=1)
        head.runs[0].font.color.rgb = tpl["primary"]

        for a in articles:
            title_p = doc.add_paragraph()
            _hyperlink_run(title_p, a.get("title", "Untitled"), tpl["primary"], bold=True)

            source = a.get("publisher") or a.get("publisher_name", "")
            when = a.get("date") or a.get("published_date", "")
            metap = doc.add_paragraph()
            metap.add_run(f"{source}  ·  {when}").italic = True

            summary = a.get("summary") or a.get("content", "")
            if summary:
                doc.add_paragraph(summary[:600])

            url = a.get("url", "")
            if url:
                doc.add_paragraph(url).runs[0].font.size = Pt(8)

        back = doc.add_paragraph()
        _hyperlink_run(back, "↑ Back to top", tpl["accent"])
        doc.add_paragraph()

    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()

    key = keys.report(session_id, f"{(brand or 'brand').lower()}_report.docx")
    await store.put_bytes(
        key, data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    log.info("report.built", session=session_id, sections=len(feed), bytes=len(data))
    return data
