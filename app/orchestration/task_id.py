"""Human-friendly Task IDs — BRAND-YYYYMMDD-NNN — used in every channel subject
so concurrent tasks (BeOne / Trane / a repeat request) each get their own thread,
and gate replies can be matched back to the exact task."""
import contextlib
import re
from datetime import UTC, datetime

_TAG = re.compile(r"\[([A-Z0-9]+-\d{8}-\d{3})\]")


def slug_for(brand: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (brand or "").upper())[:8] or "TASK"


def task_tag(task_id: str) -> str:
    return f"[{task_id}]"


def extract_task_id(text: str) -> str | None:
    m = _TAG.search(text or "")
    return m.group(1) if m else None


async def make_task_id(brand: str) -> str:
    """BRAND-YYYYMMDD-NNN with a per-brand-per-day sequence from Redis."""
    slug = slug_for(brand)
    day = datetime.now(UTC).strftime("%Y%m%d")
    seq = await _next_seq(slug, day)
    return f"{slug}-{day}-{seq:03d}"


async def _next_seq(slug: str, day: str) -> int:
    import redis.asyncio as aioredis

    from app.config.settings import get_settings

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        key = f"taskseq:{slug}:{day}"
        n = await r.incr(key)
        await r.expire(key, 3 * 24 * 3600)
        return int(n)
    except Exception:
        return 1
    finally:
        with contextlib.suppress(Exception):
            await r.aclose()
