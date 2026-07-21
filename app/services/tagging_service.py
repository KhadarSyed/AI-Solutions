"""Stage 3 — XAI tagging: reach enrichment → batched 18-field tool calls with
correction bias → confidence reorder → A0…An ids → syndication links →
tagged_file + embeddings."""

import asyncio
import uuid

from sqlalchemy import update

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Session as SessionRow
from app.llm_gateway.guarded import GuardedAgent
from app.memory import vector_store
from app.memory.correction_memory import build_bias_block
from app.observability.logging import get_logger
from app.tools.enrichment.reach import enrich_reach
from app.tools.schemas.tagging import TAGGING_SYSTEM_PROMPT, TagBatchOutput

log = get_logger(__name__)


def _batch_prompt(
    articles: list[dict], brand: str, competitors: list[str], sections: list[str], bias: str,
    additions: list[str] | None = None,
) -> str:
    lines = [
        f"Brand of interest: {brand}",
        f"Known competitors: {', '.join(competitors) or 'none provided'}",
        f"Allowed sections: {', '.join(sections)}",
    ]
    if additions:
        lines.append(
            "User-requested extra data points to capture (note them in the relevant reason "
            "fields and, where they map to a signal/theme, reflect them): "
            + "; ".join(additions))
    if bias:
        lines.append("")
        lines.append(bias)
    lines.append("\nArticles:")
    for i, a in enumerate(articles):
        body = (a.get("content") or "")[:1200]
        lines.append(
            f"[{i}] title: {a.get('title', '')}\n"
            f"    publisher: {a.get('publisher_name', '')} ({a.get('publisher_domain', '')})\n"
            f"    date: {a.get('published_date', '')}\n    text: {body}"
        )
    return "\n".join(lines)


async def tag_session(*, session_id: str, project_id: str) -> dict:
    settings = get_settings()
    store = get_artifact_store()

    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            update(SessionRow).where(SessionRow.id == uuid.UUID(session_id))
            .values(status="tagging")
        )

    source = await store.get_json(keys.source_file(session_id))
    articles: list[dict] = source["articles"]
    syndication: dict = source.get("syndication", {})

    async with get_sessionmaker()() as db:
        row = await db.get(SessionRow, uuid.UUID(session_id))
        config = row.config if row else {}
    brand = config.get("brand", "")
    competitors = config.get("competitors", [])
    sections = config.get("sections") or ["Brand News", "Competitors News", "Industry News"]
    # user-requested extra data points: this run (session config) + inherited (project)
    additions = list(config.get("tagging_additions", []))
    try:
        from app.db.models import Project

        async with get_sessionmaker()() as db:
            proj = await db.get(Project, uuid.UUID(project_id))
        additions = list(dict.fromkeys([*additions, *((proj.tagging_notes or []) if proj else [])]))
    except Exception:
        pass

    # reach enrichment (deterministic)
    reach = await enrich_reach([a.get("publisher_domain", "") for a in articles])
    for a in articles:
        a.update(reach.get(a.get("publisher_domain", "").lower().removeprefix("www."), {}))

    bias = await build_bias_block(project_id)

    tagger = GuardedAgent(
        purpose="tag_articles_batch",
        stage="tagging",
        system_prompt=TAGGING_SYSTEM_PROMPT,
        output_type=TagBatchOutput,
        temperature=0.0,
    )

    batch_size = settings.llm_batch_size
    batches = [articles[i:i + batch_size] for i in range(0, len(articles), batch_size)]
    sem = asyncio.Semaphore(settings.llm_concurrency)
    failures: list[str] = []

    async def tag_batch(offset: int, batch: list[dict]) -> None:
        async with sem:
            try:
                out = await tagger.run(
                    _batch_prompt(batch, brand, competitors, sections, bias, additions))
            except Exception as exc:
                failures.append(f"batch@{offset}: {exc}")
                return
            for tagged in out.articles:
                if 0 <= tagged.index < len(batch):
                    batch[tagged.index].update(
                        tagged.model_dump(exclude={"index"}) | {"entities": tagged.entities.model_dump()}
                    )

    await asyncio.gather(*(tag_batch(i * batch_size, b) for i, b in enumerate(batches)))

    tagged = [a for a in articles if "xai_sentiment" in a]
    # confidence reorder + id reassignment A0…An
    tagged.sort(key=lambda a: a.get("relevancy_confidence", 0), reverse=True)
    fp_by_url = {}
    for fp, urls in syndication.items():
        for u in urls:
            fp_by_url[u] = fp
    for i, a in enumerate(tagged):
        a["id"] = f"A{i}"
        a["is_approved"] = False
        a["is_approved_for_monitoring"] = False
        a["syndicated_urls"] = syndication.get(fp_by_url.get(a.get("url", ""), ""), [])

    payload = {"articles": tagged, "stats": {
        "input": len(articles), "tagged": len(tagged), "failed_batches": failures,
        "bias_rules_applied": bias.count("\n- ") + (1 if bias else 0),
    }}
    key = keys.tagged_file(session_id)
    await store.put_json(key, payload)

    embedded = 0
    try:
        embedded = await vector_store.upsert_articles(project_id, session_id, tagged)
    except Exception as exc:
        log.warning("tagging.embed_failed", error=str(exc)[:200])

    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            update(SessionRow).where(SessionRow.id == uuid.UUID(session_id))
            .values(status="tagged", tagged_file_key=key, articles_count=len(tagged))
        )

    stats = payload["stats"] | {"embedded": embedded}
    log.info("tagging.done", session=session_id, **{k: v for k, v in stats.items()
                                                    if isinstance(v, int | str)})
    return stats
