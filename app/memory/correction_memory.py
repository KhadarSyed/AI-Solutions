"""Correction memory — the reward signal of the reinforcement loop.

Structured events live in SQL (correction_events); at tagging time the last 200
per project are grouped into a few-shot bias block; distillation into Mem0
feedback memories happens at stage boundaries (capped)."""

import contextlib
import uuid
from collections import Counter, defaultdict

from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.db.models import CorrectionEvent
from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger

log = get_logger(__name__)

BIAS_WINDOW = 200
MAX_RULES_IN_PROMPT = 12


async def log_correction(
    *,
    project_id: str,
    session_id: str,
    article_id: str,
    field_name: str,
    original_value: str | None,
    corrected_value: str | None,
    original_confidence: float | None,
    article_features: dict,
    event_type: str = "edit",
) -> None:
    async with get_sessionmaker()() as db, db.begin():
        db.add(
            CorrectionEvent(
                project_id=uuid.UUID(project_id),
                session_id=uuid.UUID(session_id),
                article_id=article_id,
                field_name=field_name,
                original_value=original_value,
                corrected_value=corrected_value,
                original_confidence=original_confidence,
                article_features=article_features,
                event_type=event_type,
            )
        )


async def recent_corrections(project_id: str, limit: int = BIAS_WINDOW) -> list[CorrectionEvent]:
    async with get_sessionmaker()() as db:
        return list(
            (
                await db.execute(
                    select(CorrectionEvent)
                    .where(CorrectionEvent.project_id == uuid.UUID(project_id))
                    .order_by(CorrectionEvent.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )


def _patterns(events: list[CorrectionEvent]) -> list[str]:
    """Group corrections into dominant, human-readable rules."""
    rules: list[tuple[int, str]] = []

    # field-value transitions, optionally conditioned on source domain
    by_transition: dict[tuple, list[CorrectionEvent]] = defaultdict(list)
    for e in events:
        if e.event_type == "edit" and e.original_value and e.corrected_value:
            by_transition[(e.field_name, e.original_value, e.corrected_value)].append(e)

    for (field, orig, corr), evs in by_transition.items():
        if len(evs) < 2:
            continue
        domains = Counter(
            (e.article_features or {}).get("domain", "") for e in evs if e.article_features
        )
        top_domain, top_count = (domains.most_common(1) or [("", 0)])[0]
        if top_domain and top_count >= max(2, len(evs) // 2):
            rules.append((len(evs),
                          f"For articles from {top_domain}: {field} '{orig}' is usually "
                          f"'{corr}' ({len(evs)} reviewer corrections)."))
        else:
            rules.append((len(evs),
                          f"{field}: reviewer changed '{orig}' → '{corr}' {len(evs)} times — "
                          f"prefer '{corr}' in comparable cases."))

    # deletion patterns → low relevancy signals
    deleted_domains = Counter(
        (e.article_features or {}).get("domain", "")
        for e in events if e.event_type == "delete" and e.article_features
    )
    for domain, n in deleted_domains.most_common(3):
        if domain and n >= 2:
            rules.append((n, f"Articles from {domain} were deleted {n} times — treat this "
                             f"source as low-relevancy unless clearly brand-related."))

    rules.sort(reverse=True)
    return [r for _, r in rules[:MAX_RULES_IN_PROMPT]]


async def build_bias_block(project_id: str) -> str:
    events = await recent_corrections(project_id)
    rules = _patterns(events) if events else []
    # fold in distilled Mem0 FEEDBACK memories so the reflection→feedback loop and
    # the tagging-bias loop are connected (feedback persists across runs/sources)
    with contextlib.suppress(Exception):
        hits = await recall(agent="correction_memory",
                            query="reviewer preference tagging rules",
                            project_id=project_id, memory_type=MemoryType.FEEDBACK, limit=8)
        for h in hits:
            text = str(h.get("memory", "")).strip()
            if text and text not in rules:
                rules.append(text)
    if not rules:
        return ""
    rules = rules[:MAX_RULES_IN_PROMPT]
    return (
        "Reviewer-preference rules learned from past corrections (follow them):\n- "
        + "\n- ".join(rules)
    )


async def distill_to_mem0(project_id: str, run_id: str | None = None, cap: int = 5) -> int:
    """Write the strongest current rules into Mem0 feedback memory (stage boundary)."""
    events = await recent_corrections(project_id)
    rules = _patterns(events)[:cap]
    written = 0
    for rule in rules:
        with contextlib.suppress(Exception):
            await remember(
                agent="correction_memory", memory_type=MemoryType.FEEDBACK,
                project_id=project_id, content=rule, run_id=run_id,
            )
            written += 1
    return written
