"""Automated per-report Vercel publishing.

On delivery, deploy the report's dashboard.html (CDN asset mode → small,
self-contained-enough) to Vercel via the REST API and return a durable URL.
No-op when VERCEL_TOKEN is unset; best-effort — never blocks delivery."""
import base64
import contextlib
import re

import httpx

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

_API = "https://api.vercel.com/v13/deployments"


def project_name(brand: str, task_id: str) -> str:
    """Vercel project names: lowercase, alphanumeric + dashes, <=100 chars."""
    raw = f"prsol-{brand}-{task_id}".lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", raw)).strip("-")[:100] or "prsol-report"


async def publish_dashboard(brand: str, task_id: str, html: bytes) -> str | None:
    s = get_settings()
    if not s.vercel_token or not html:
        return None
    name = project_name(brand, task_id)
    payload = {
        "name": name,
        "files": [{"file": "index.html",
                   "data": base64.b64encode(html).decode(), "encoding": "base64"}],
        "projectSettings": {"framework": None},
        "target": "production",
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                _API, headers={"Authorization": f"Bearer {s.vercel_token}"}, json=payload)
        if resp.status_code >= 300:
            log.info("vercel.publish_failed", status=resp.status_code, body=resp.text[:200])
            return None
        # the deployment-specific *.-team.vercel.app URL is SSO-gated; the project's
        # canonical production alias {name}.vercel.app is the public one
        url = f"https://{name}.vercel.app"
        log.info("vercel.published", name=name, url=url)
        return url
    except Exception as exc:
        log.info("vercel.publish_error", error=str(exc)[:200])
        return None


async def publish_from_artifact(session_id: str, brand: str, task_id: str) -> str | None:
    """Read the stored dashboard.html and publish it. Returns the URL or None."""
    with contextlib.suppress(Exception):
        from app.artifacts.factory import get_artifact_store

        html = await get_artifact_store().get_bytes(f"reports/{session_id}/dashboard.html")
        return await publish_dashboard(brand, task_id, html)
    return None
