"""Staged CSS-styled updates — each pipeline stage narrates itself to the origin channel.

The message body uses inline-styled CSS bar charts + tables (they render in every email
client and in Teams — no JS, no remote assets). A companion self-contained ECharts HTML
snapshot rides along as an attachment for the interactive view. Every number comes from
app.analytics.stage_stats (deterministic), so nothing here can invent data."""

import html
import json
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[1] / "static" / "vendor"

# Apple×editorial palette (email-safe: inline only, system + serif fonts)
_BG = "#f5f2ec"
_CARD = "#ffffff"
_BORDER = "#ece7df"
_INK = "#1d1d1f"
_MUTED = "#6b6b70"
_ACCENT = "#b5462f"
_TRACK = "#efece6"
_BODY_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif")
_SERIF = "Georgia,'Times New Roman',serif"


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _bars(pairs: list[tuple[str, int]], accent: str = _ACCENT) -> str:
    if not pairs:
        return f'<p style="color:{_MUTED};font-size:13px;margin:6px 0;">No data yet.</p>'
    top = max((v for _, v in pairs), default=0) or 1
    rows = []
    for label, value in pairs:
        pct = round(100 * value / top)
        rows.append(
            f'<div style="margin:7px 0;">'
            f'<div style="display:flex;justify-content:space-between;font-size:13px;'
            f'color:{_INK};"><span>{_esc(label)}</span>'
            f'<span style="color:{_MUTED};">{value}</span></div>'
            f'<div style="background:{_TRACK};border-radius:6px;height:9px;margin-top:3px;">'
            f'<div style="width:{pct}%;background:{accent};height:9px;border-radius:6px;">'
            f'</div></div></div>'
        )
    return "".join(rows)


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return f'<p style="color:{_MUTED};font-size:13px;">No data yet.</p>'
    th = "".join(
        f'<th style="text-align:left;padding:6px 8px;border-bottom:2px solid {_BORDER};'
        f'font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:{_MUTED};">'
        f"{_esc(h)}</th>" for h in headers)
    body = []
    for r in rows:
        tds = "".join(
            f'<td style="padding:6px 8px;border-bottom:1px solid {_BORDER};font-size:13px;'
            f'color:{_INK};">{_esc(c)}</td>' for c in r)
        body.append(f"<tr>{tds}</tr>")
    return (f'<table style="width:100%;border-collapse:collapse;margin-top:4px;">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>")


def _summary(text: str) -> str:
    lines = "".join(
        f'<div style="font-size:12.5px;color:{_MUTED};line-height:1.5;">{_esc(ln)}</div>'
        for ln in text.split("\n") if ln.strip())
    return (f'<div style="margin-top:12px;padding-top:10px;'
            f'border-top:1px dashed {_BORDER};">{lines}</div>')


def _card(title: str, content: str, summary: str = "") -> str:
    return (
        f'<div style="background:{_CARD};border:1px solid {_BORDER};border-radius:14px;'
        f'padding:18px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.04);">'
        f'<h2 style="font-family:{_SERIF};font-size:17px;color:{_INK};margin:0 0 10px;">'
        f"{_esc(title)}</h2>{content}{_summary(summary) if summary else ''}</div>"
    )


def _shell(task_id: str, brand: str, stage_no: int, stage_total: int, phase: str,
           title: str, subtitle: str, body: str) -> str:
    tag = f"[{task_id}] " if task_id else ""
    return (
        f'<div style="background:{_BG};padding:26px 16px;font-family:{_BODY_FONT};">'
        f'<div style="max-width:660px;margin:0 auto;">'
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.14em;color:{_ACCENT};">Stage {stage_no} / {stage_total} · '
        f"{_esc(phase)}</div>"
        f'<h1 style="font-family:{_SERIF};font-size:24px;color:{_INK};margin:6px 0 2px;">'
        f"{_esc(tag)}{_esc(brand)} Monitoring</h1>"
        f'<p style="color:{_MUTED};font-size:14px;margin:0 0 6px;">{_esc(subtitle)}</p>'
        f"{body}</div></div>"
    )


def collection_html(task_id: str, brand: str, stage_total: int, stats: dict) -> str:
    body = _card(
        "Coverage found by group",
        _bars(stats["group_series"]),
        "",
    ) + _card(
        "By brand & competitor",
        _bars(stats["subject_series"]),
        stats["summary"],
    )
    subtitle = (f"Found {stats['total']} articles. Review the attached CSV, then reply "
                "APPROVE to tag — or reply with an edited CSV to exclude rows.")
    return _shell(task_id, brand, 1, stage_total, "Collection · awaiting approval",
                  f"{brand} Monitoring", subtitle, body)


def tagging_html(task_id: str, brand: str, stage_total: int, stats: dict) -> str:
    theme_rows = [[t, n] for t, n in stats["top_themes"]]
    pub_rows = [[p, n] for p, n in stats["top_publications"]]
    author_rows = [[a, n] for a, n in stats["top_authors"]]
    peak_rows = [[d, n] for d, n in stats["volume_peaks"]]
    body = (
        _card("Top 5 themes", _table(["Theme", "Articles"], theme_rows))
        + _card("Top 5 publications", _table(["Publication", "Articles"], pub_rows))
        + _card("Top 5 authors", _table(["Author", "Articles"], author_rows))
        + _card("Busiest days (top 3 volume peaks)",
                _table(["Date", "Articles"], peak_rows), stats["summary"])
    )
    subtitle = (f"Tagged {stats['tagged']} articles ({stats['enriched']} enriched). Review "
                "the attached tagged CSV; reply APPROVE, or reply with an edited CSV to set "
                "Monitoring=FALSE on any rows you want excluded from the dashboard.")
    return _shell(task_id, brand, 2, stage_total, "Tagging · awaiting approval",
                  f"{brand} Monitoring", subtitle, body)


def final_html(task_id: str, brand: str, stage_total: int, stats: dict,
               dashboard_url: str = "") -> str:
    link = ""
    if dashboard_url:
        link = (f'<a href="{_esc(dashboard_url)}" style="display:inline-block;margin-top:6px;'
                f'background:{_ACCENT};color:#fff;text-decoration:none;padding:10px 18px;'
                f'border-radius:9px;font-size:14px;">Open the live dashboard →</a>')
    body = _card(
        "Delivered",
        _bars([("In dashboard", stats["in_dashboard"]), ("Excluded", stats["dropped"])])
        + link,
        stats["summary"],
    )
    subtitle = (f"Your {brand} dashboard is ready — {stats['in_dashboard']} monitored "
                f"articles, {stats['dropped']} excluded.")
    return _shell(task_id, brand, stage_total, stage_total, "Delivery · complete",
                  f"{brand} Monitoring", subtitle, body)


def echarts_snapshot(title: str, sections: list[dict]) -> bytes:
    """Self-contained interactive HTML (embedded ECharts) for the stage's charts.

    sections: [{"id","title","type":"bar"|"line","categories":[...],"values":[...]}]"""
    echarts_js = (_VENDOR / "echarts.min.js").read_text(encoding="utf-8")
    divs, inits = [], []
    for s in sections:
        cid = s["id"]
        divs.append(
            f'<section><h2>{_esc(s["title"])}</h2>'
            f'<div id="{cid}" style="width:100%;height:340px;"></div></section>')
        option = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 60, "right": 24, "top": 24, "bottom": 70},
            "xAxis": {"type": "category", "data": s["categories"],
                      "axisLabel": {"rotate": 35 if s["type"] == "line" else 0}},
            "yAxis": {"type": "value"},
            "series": [{"type": s["type"], "data": s["values"],
                        "smooth": s["type"] == "line",
                        "itemStyle": {"color": _ACCENT},
                        "areaStyle": {} if s["type"] == "line" else None}],
        }
        inits.append(
            f'echarts.init(document.getElementById("{cid}")).setOption('
            f"{json.dumps(option)});")
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>"
        f"{_esc(title)}</title>"
        f"<style>body{{background:{_BG};font-family:{_BODY_FONT};color:{_INK};margin:0;"
        f"padding:24px;}}h1{{font-family:{_SERIF};}}section{{background:{_CARD};border:1px "
        f"solid {_BORDER};border-radius:14px;padding:16px;margin:16px auto;max-width:900px;}}"
        f"h2{{font-family:{_SERIF};font-size:16px;}}</style></head><body>"
        f"<h1>{_esc(title)}</h1>"
        f"{''.join(divs)}"
        f"<script>{echarts_js}</script><script>{''.join(inits)}</script>"
        "</body></html>"
    )
    return doc.encode("utf-8")
