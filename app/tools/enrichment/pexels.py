"""Pexels brand-relevant video for the dashboard hero banner.

Searches the Pexels video API by brand/industry, returns a landscape clip
(video_url + poster + author). Enabled by PEXELS_API_KEY. Best-effort — the
dashboard falls back to a gradient banner when this returns None."""
import contextlib

import httpx

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

_ENDPOINT = "https://api.pexels.com/videos/search"


def _pick_video(payload: dict) -> dict | None:
    """Choose the best landscape clip and its highest-quality file."""
    videos = (payload or {}).get("videos") or []
    if not videos:
        return None
    # prefer a landscape video
    videos.sort(key=lambda v: (v.get("width", 0) >= v.get("height", 1), v.get("width", 0)),
                reverse=True)
    v = videos[0]
    files = v.get("video_files") or []
    if not files:
        return None
    # highest-resolution file (hd/uhd before sd)
    files.sort(key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), reverse=True)
    link = files[0].get("link")
    if not link:
        return None
    return {"video_url": link, "poster": v.get("image", ""),
            "author": (v.get("user") or {}).get("name", "")}


async def brand_video(brand: str, industry: str | None = None) -> dict | None:
    s = get_settings()
    if not s.pexels_api_key:
        return None
    query = f"{brand} {industry}".strip() if industry else brand
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _ENDPOINT,
                params={"query": query, "orientation": "landscape",
                        "size": "medium", "per_page": 5},
                headers={"Authorization": s.pexels_api_key},
            )
            resp.raise_for_status()
        picked = _pick_video(resp.json())
        if picked is None and industry:
            # retry on the industry term alone (brand names rarely have stock video)
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        _ENDPOINT,
                        params={"query": industry, "orientation": "landscape",
                                "size": "medium", "per_page": 5},
                        headers={"Authorization": s.pexels_api_key},
                    )
                    resp.raise_for_status()
                picked = _pick_video(resp.json())
        return picked
    except Exception as exc:
        log.info("pexels.failed", error=str(exc)[:150])
        return None
