"""Staged, industry-standard channel updates — each pipeline stage narrates itself
in-thread with a clear, interactive email.

Design goals: the reader always knows (1) where they are (stage stepper), (2) what they're
looking at (KPI-cards-first, plain-language captions), and (3) exactly what to reply (one
CTA box). Layout is table-based + inline CSS so it renders in Outlook/Gmail/Apple Mail and
on mobile. Charts in the body are pure-CSS bars (no JS, no data-URI images which email
strips); a self-contained ECharts snapshot rides along as an attachment. Every number
comes from app.analytics.stage_stats (deterministic) — nothing here can invent data."""

import html
import json
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[1] / "static" / "vendor"

# Apple × editorial palette (email-safe)
_BG = "#f5f2ec"
_CARD = "#ffffff"
_BORDER = "#ece7df"
_INK = "#1d1d1f"
_MUTED = "#6b6b70"
_ACCENT = "#b5462f"
_TRACK = "#efece6"
_POS = "#2f8f5b"
_NEG = "#b5462f"
_NEU = "#b8b2a7"
_BODY_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
_SERIF = "Georgia,'Times New Roman',serif"

_STEPS = ["Plan", "Collect", "Tag", "Report"]


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _stepper(active: int) -> str:
    """✓ done · ● current · ○ upcoming — renders in every client."""
    parts = []
    for i, name in enumerate(_STEPS):
        if i < active:
            parts.append(f'<span style="color:{_ACCENT}">&#10003; {_esc(name)}</span>')
        elif i == active:
            parts.append(f'<span style="color:{_ACCENT};font-weight:700">&#9679; '
                         f'{_esc(name)}</span>')
        else:
            parts.append(f'<span style="color:{_MUTED}">&#9675; {_esc(name)}</span>')
    return ('<div style="font-size:12px;letter-spacing:.02em;margin:2px 0 14px">'
            + '<span style="color:#cfc9bd"> &nbsp;&rsaquo;&nbsp; </span>'.join(parts)
            + "</div>")


def _preheader(text: str) -> str:
    return (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">'
            f"{_esc(text)}</div>")


def _card(title: str, content: str, summary: str = "") -> str:
    cap = ""
    if summary:
        lines = "".join(
            f'<div style="font-size:12.5px;color:{_MUTED};line-height:1.5">{_esc(ln)}</div>'
            for ln in summary.split("\n") if ln.strip())
        cap = (f'<div style="margin-top:12px;padding-top:10px;border-top:1px dashed '
               f'{_BORDER}">{lines}</div>')
    head = (f'<h2 style="font-family:{_SERIF};font-size:17px;color:{_INK};margin:0 0 10px">'
            f"{_esc(title)}</h2>" if title else "")
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{_CARD};border:1px solid {_BORDER};border-radius:14px;'
            f'margin:14px 0"><tr><td style="padding:18px 20px">{head}{content}{cap}'
            f"</td></tr></table>")


def _bars(pairs, accent: str = _ACCENT) -> str:
    if not pairs:
        return f'<p style="color:{_MUTED};font-size:13px;margin:6px 0">No data yet.</p>'
    top = max((v for _, v in pairs), default=0) or 1
    rows = []
    for label, value in pairs:
        pct = round(100 * value / top)
        rows.append(
            f'<tr><td style="font-size:13px;color:{_INK};padding:6px 8px 2px 0">{_esc(label)}'
            f'</td><td align="right" style="font-size:13px;color:{_MUTED};'
            f'padding:6px 0 2px">{value}</td></tr>'
            f'<tr><td colspan="2" style="padding:0 0 6px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{_TRACK};border-radius:6px"><tr>'
            f'<td style="width:{pct}%;background:{accent};height:9px;line-height:9px;'
            f'border-radius:6px;font-size:0">&nbsp;</td>'
            f'<td style="font-size:0">&nbsp;</td></tr></table></td></tr>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f"{''.join(rows)}</table>")


def _table(headers, rows) -> str:
    if not rows:
        return f'<p style="color:{_MUTED};font-size:13px">No data yet.</p>'
    th = "".join(
        f'<th align="left" style="padding:6px 8px;border-bottom:2px solid {_BORDER};'
        f'font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:{_MUTED}">'
        f"{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td style="padding:6px 8px;border-bottom:1px solid {_BORDER};font-size:13px;'
            f'color:{_INK}">{_esc(c)}</td>' for c in r) + "</tr>" for r in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f"style=\"border-collapse:collapse\"><thead><tr>{th}</tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _kpi_cards(cards) -> str:
    """cards: [(value, label)] — a responsive row of stat cards. The value font shrinks
    for long strings (e.g. a date-range window) so it never overflows the card."""
    cells = []
    for value, label in cards:
        n = len(str(value))
        fs = 26 if n <= 8 else 20 if n <= 14 else 15 if n <= 24 else 13
        cells.append(
            f'<td width="{100 // max(len(cards), 1)}%" valign="top" style="padding:6px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{_CARD};border:1px solid {_BORDER};border-radius:12px">'
            f'<tr><td style="padding:14px 14px 12px">'
            f'<div style="font-family:{_SERIF};font-size:{fs}px;color:{_INK};line-height:1.15">'
            f"{_esc(value)}</div>"
            f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;'
            f'color:{_MUTED};margin-top:6px">{_esc(label)}</div>'
            f"</td></tr></table></td>")
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f"<tr>{''.join(cells)}</tr></table>")


def _logo_row(brands) -> str:
    """brands: [(name, logo_url|None, color)] — remote logo img (email clients load it)
    over a colored cell so blocked images still show the name/initial."""
    cells = []
    for name, logo_url, color in brands:
        initial = _esc((name or "?")[:1].upper())
        inner = (f'<img src="{_esc(logo_url)}" alt="{_esc(name)}" width="40" height="40" '
                 f'style="border-radius:8px;display:block;margin:0 auto 6px">'
                 if logo_url else
                 f'<div style="width:40px;height:40px;line-height:40px;border-radius:8px;'
                 f'background:{color or _ACCENT};color:#fff;font-weight:700;font-size:18px;'
                 f'text-align:center;margin:0 auto 6px">{initial}</div>')
        cells.append(
            f'<td valign="top" align="center" style="padding:8px">'
            f'{inner}<div style="font-size:12px;color:{_INK}">{_esc(name)}</div></td>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
            f"{''.join(cells)}</tr></table>")


def _cta(options) -> str:
    """options: [(bold_label, description)] — the single 'what to reply' box."""
    items = "".join(
        f'<li style="margin:4px 0;font-size:13.5px;color:{_INK}">'
        f'<strong>{_esc(a)}</strong> — {_esc(b)}</li>' for a, b in options)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:#fbf7f2;border:1px solid {_BORDER};border-left:4px solid '
            f'{_ACCENT};border-radius:10px;margin:16px 0"><tr><td style="padding:14px 18px">'
            f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;'
            f'color:{_ACCENT};font-weight:700;margin-bottom:6px">What to do next — just '
            f'reply to this email</div><ul style="margin:6px 0 0;padding-left:18px">{items}'
            f"</ul></td></tr></table>")


def _button(label: str, url: str) -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0">'
            f'<tr><td style="background:{_ACCENT};border-radius:10px">'
            f'<a href="{_esc(url)}" style="display:inline-block;padding:12px 22px;color:#fff;'
            f'text-decoration:none;font-size:14px;font-weight:600">{_esc(label)}</a>'
            f"</td></tr></table>")


def _shell(task_id: str, brand: str, active_step: int, phase: str, subtitle: str,
           body: str, preheader: str, footer: str = "") -> str:
    tag = f"[{task_id}] " if task_id else ""
    return (
        f"<style>@media (max-width:620px){{.kpi td{{display:block;width:100%!important}}}}"
        f"</style>{_preheader(preheader)}"
        f'<div style="background:{_BG};padding:24px 14px;font-family:{_BODY_FONT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        f'style="max-width:640px;width:100%"><tr><td>'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;'
        f'color:{_MUTED}">{_esc(tag)}{_esc(brand)} Monitoring</div>'
        f"{_stepper(active_step)}"
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:.12em;color:{_ACCENT}">{_esc(phase)}</div>'
        f'<h1 style="font-family:{_SERIF};font-size:23px;color:{_INK};margin:6px 0 4px">'
        f"{_esc(brand)} Monitoring</h1>"
        f'<p style="color:{_MUTED};font-size:14px;margin:0 0 8px;line-height:1.5">'
        f"{_esc(subtitle)}</p>"
        f"{body}{footer}"
        f'<div style="color:#a7a19a;font-size:11px;margin-top:16px">PR Intelligence Agent · '
        f"replies stay in this thread · this step stays open until you reply.</div>"
        f"</td></tr></table></td></tr></table></div>"
    )


def status_html(task_id, brand, agent, phase, message, *, icon: str = "") -> str:
    """A small branded card for an agent status update (started/finished), so these short
    in-thread notes match the styled stage emails instead of arriving as plain text."""
    tag = f"[{task_id}] " if task_id else ""
    dot = _POS if phase == "finished" else _ACCENT
    return (
        f"{_preheader(f'{agent} {phase} — {message[:80]}')}"
        f'<div style="background:{_BG};padding:18px 14px;font-family:{_BODY_FONT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="center"><table role="presentation" width="640" cellpadding="0" '
        f'cellspacing="0" style="max-width:640px;width:100%"><tr><td>'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;'
        f'color:{_MUTED}">{_esc(tag)}{_esc(brand)} Monitoring</div>'
        f'<div style="background:{_CARD};border:1px solid {_BORDER};border-left:3px solid '
        f'{dot};border-radius:12px;padding:14px 16px;margin-top:10px">'
        f'<div style="font-family:{_SERIF};font-size:15px;color:{_INK};margin:0 0 4px">'
        f'{_esc(icon)} {_esc(agent)} Agent — {_esc(phase)}</div>'
        f'<div style="font-size:13px;color:{_MUTED};line-height:1.55">{_esc(message)}</div>'
        f'</div>'
        f'<div style="color:#a7a19a;font-size:11px;margin-top:12px">PR Intelligence Agent · '
        f'replies stay in this thread.</div>'
        f'</td></tr></table></td></tr></table></div>'
    )


def ack_html(task_id, brand, message, *, brands_logos=None) -> str:
    """A styled acknowledgement card (task started / approval recorded / answer), threaded
    in the task conversation. When brands_logos is given it shows the brand (+ competitor)
    logos — the same 'in scope' row as the Plan email."""
    tag = f"[{task_id}] " if task_id else ""
    scope = _card("In scope", _logo_row(brands_logos)) if brands_logos else ""
    return (
        f"{_preheader(str(message)[:90])}"
        f'<div style="background:{_BG};padding:18px 14px;font-family:{_BODY_FONT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="center"><table role="presentation" width="640" cellpadding="0" '
        f'cellspacing="0" style="max-width:640px;width:100%"><tr><td>'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;'
        f'color:{_MUTED}">{_esc(tag)}{_esc(brand)} Monitoring</div>'
        f'<div style="background:{_CARD};border:1px solid {_BORDER};border-left:3px solid '
        f'{_ACCENT};border-radius:12px;padding:16px;margin-top:10px">'
        f'<div style="font-size:14px;color:{_INK};line-height:1.6">{_esc(message)}</div>'
        f'</div>{scope}'
        f'<div style="color:#a7a19a;font-size:11px;margin-top:12px">PR Intelligence Agent · '
        f'replies stay in this thread.</div>'
        f'</td></tr></table></td></tr></table></div>'
    )


def plan_html(task_id, brand, stats, *, brands_logos, duration, intent, goal,
              boolean_queries) -> str:
    q_rows = [[q] for q in boolean_queries[:12]]
    body = (
        _kpi_cards([(str(len(brands_logos)), "Brands tracked"),
                    (duration, "Window"),
                    (str(len(boolean_queries)), "Search queries")])
        + _card("Brands in scope", _logo_row(brands_logos))
        + _card("What this run will do", f'<div style="font-size:13.5px;color:{_INK};'
                f'line-height:1.6">{_esc(intent)}</div>')
        + _card("Goal", f'<div style="font-size:13.5px;color:{_INK};line-height:1.6">'
                f"{_esc(goal)}</div>")
        + _card("Search queries generated", _table(["Boolean query"], q_rows))
        + _cta([("approve", "start collecting coverage now"),
                ("change: …", "e.g. 'change: add competitor Carrier', 'change: last 14 days'"),
                ]))
    return _shell(task_id, brand, 0, "Stage 1 · Plan — review & approve",
                  f"Monitoring begins for {brand}. Here's the plan — review and approve, "
                  "or tell me what to change.", body,
                  preheader=f"Plan ready for {brand} — approve or request changes.")


def collection_kpi_html(task_id, brand, stats, *, tagging_sources, enrichment_points) -> str:
    body = (
        _kpi_cards([(str(stats["total"]), "Articles"),
                    (str(stats["country_count"]), "Countries"),
                    (str(len(stats["top_publications"])), "Top publications")])
        + _card("Coverage by group", _bars(stats["group_series"]))
        + _card("By brand & competitor", _bars(stats["subject_series"]), stats["summary"])
        + _card("Top 5 publications", _table(["Publication", "Articles"],
                [[p, n] for p, n in stats["top_publications"]]))
        + _card("Top 5 authors", _table(["Author", "Articles"],
                [[a, n] for a, n in stats["top_authors"]]))
        + _card("Next: tagging plan",
                f'<div style="font-size:13.5px;color:{_INK};line-height:1.6">On approval I '
                f'tag every article for sentiment (+confidence & reason), theme tiers, '
                f'emotions, signals and entities.<br><b>Sources:</b> {_esc(tagging_sources)}'
                f'<br><b>Enrichment:</b> {_esc(enrichment_points)}</div>')
        + _cta([("approve", "start tagging with the plan above"),
                ("add: …", "add a data point to tag, e.g. 'add: pricing mentions' "
                 "(I'll remember it for future runs too)"),
                ("change: …", "adjust the collection")]))
    return _shell(task_id, brand, 1, "Stage 2 · Collection — review & approve",
                  f"Collected {stats['total']} articles for {brand}. Review the KPIs and the "
                  "attached CSV, then approve to tag.", body,
                  preheader=f"{stats['total']} articles collected for {brand} — approve to tag.")


def tagged_results_html(task_id, brand, stats, breakdown, *, collected=0, dropped=0,
                        memory_updates=None) -> str:
    memory_updates = memory_updates or []
    tagged = stats["tagged"]
    collected = collected or (tagged + dropped)
    sov = breakdown["sov_series"]
    sent = breakdown["sentiment_series"]
    sent_rows = [[n, s["POS"], s["NEU"], s["NEG"]] for n, s in sent]
    mem_txt = ("Applied your requests: " + ", ".join(memory_updates)
               if memory_updates else "No new tagging rules requested.")
    funnel = _card(
        "From collection to tagged",
        f'<div style="font-size:13.5px;color:{_INK};line-height:1.7">'
        f'<b>{collected}</b> articles collected &rarr; <b>{tagged}</b> tagged and '
        f'classified &rarr; <b>{dropped}</b> dropped (duplicates / off-topic / failed to '
        f'tag). {stats["enriched"]} enriched with author &amp; country.</div>')
    body = (
        _kpi_cards([(str(collected), "Collected"),
                    (str(tagged), "Tagged"),
                    (str(dropped), "Dropped")])
        + funnel
        + _card("Top 5 themes", _bars(stats["top_themes"]))
        + _card("Top 5 signals", _bars(stats["top_signals"]))
        + _card("Share of voice (by brand)", _bars(sov))
        + _card("Sentiment by brand", _table(["Brand", "Positive", "Neutral", "Negative"],
                sent_rows), stats["summary"])
        + _card("Tagging memory", f'<div style="font-size:13px;color:{_MUTED}">'
                f"{_esc(mem_txt)}</div>")
        + _cta([("approve", "build the dashboard from the monitored set"),
                ("edited CSV", "attach the tagged CSV with Monitoring=FALSE on rows to "
                 "exclude"),
                ("change: …", "adjust tags or scope")]))
    return _shell(task_id, brand, 2, "Stage 3 · Tagging — sign off",
                  f"Tagged {stats['tagged']} articles ({dropped} dropped). Review the themes, "
                  "signals, SOV and sentiment, then sign off to build the dashboard.", body,
                  preheader=f"{brand} tagging done — sign off to build the dashboard.")


def home_snapshot_html(task_id, brand, stats, breakdown, *, dashboard_url,
                       in_dashboard, dropped) -> str:
    button = (_button("View the full report in your browser", dashboard_url)
              if dashboard_url else "")
    body = (
        _kpi_cards([(str(in_dashboard), "In dashboard"),
                    (str(stats.get("tagged", in_dashboard)), "Tagged"),
                    (str(dropped), "Excluded")])
        + _card("Themes leading the coverage", _bars(stats.get("top_themes", [])))
        + _card("Share of voice", _bars(breakdown.get("sov_series", [])))
        + _card("Your report is ready",
                f'<div style="font-size:13.5px;color:{_INK};line-height:1.6;margin-bottom:6px">'
                f"The interactive dashboard covers <b>Daily Monitoring</b> and "
                f"<b>Media Monitoring</b> (narrative, PR impact, reputation). Open it in any "
                f"browser — no login.</div>{button}")
        + _cta([("reply with a question", "I'll answer from the analyzed coverage (3-day "
                 "window)"), ("change: …", "regenerate with adjustments")]))
    return _shell(task_id, brand, 3, "Complete · Report delivered",
                  f"Your {brand} media analysis is complete — {in_dashboard} monitored "
                  f"articles.", body,
                  preheader=f"{brand} report ready — view the full dashboard.")


def echarts_snapshot(title: str, sections: list[dict]) -> bytes:
    """Self-contained interactive HTML (embedded ECharts) for the stage's charts.
    sections: [{"id","title","type":"bar"|"line","categories":[...],"values":[...]}]"""
    echarts_js = (_VENDOR / "echarts.min.js").read_text(encoding="utf-8")
    divs, inits = [], []
    for s in sections:
        cid = s["id"]
        divs.append(f'<section><h2>{_esc(s["title"])}</h2>'
                    f'<div id="{cid}" style="width:100%;height:340px"></div></section>')
        option = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 60, "right": 24, "top": 24, "bottom": 80},
            "xAxis": {"type": "category", "data": s["categories"],
                      "axisLabel": {"rotate": 35}},
            "yAxis": {"type": "value"},
            "series": [{"type": s["type"], "data": s["values"],
                        "smooth": s["type"] == "line", "itemStyle": {"color": _ACCENT},
                        "areaStyle": {} if s["type"] == "line" else None}],
        }
        inits.append(f'echarts.init(document.getElementById("{cid}")).setOption('
                     f"{json.dumps(option)});")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>body{{background:{_BG};font-family:{_BODY_FONT};"
        f"color:{_INK};margin:0;padding:24px}}h1{{font-family:{_SERIF}}}section{{background:"
        f"{_CARD};border:1px solid {_BORDER};border-radius:14px;padding:16px;margin:16px auto;"
        f"max-width:900px}}h2{{font-family:{_SERIF};font-size:16px}}</style></head><body>"
        f"<h1>{_esc(title)}</h1>{''.join(divs)}"
        f"<script>{echarts_js}</script><script>{''.join(inits)}</script></body></html>"
    ).encode()
