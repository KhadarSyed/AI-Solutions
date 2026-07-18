"""Serves the live-monitor UI shell. The page itself is public; all data it
loads (/runs, /ws/runs) is API-key gated, and the key is entered in the page."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["monitor"])

_HTML = (Path(__file__).resolve().parents[2] / "static" / "monitor.html").read_text(
    encoding="utf-8"
)


@router.get("/monitor", response_class=HTMLResponse)
async def monitor() -> HTMLResponse:
    return HTMLResponse(_HTML)
