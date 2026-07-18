"""WS /ws/query-builder — Stage 1 conversation.

Every outbound frame is the strict envelope {message, data, complete, options}.
State snapshots to Redis each turn (1h TTL) so a dropped connection resumes with
?resume=<token>. GeneratedQuery + Session are created ONLY on the explicit
{"action": "save"} message.
"""

import contextlib
import json
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.agents.query_builder import QBState, run_turn
from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import GeneratedQuery
from app.db.models import Session as SessionRow
from app.guardrails.runner import GuardrailViolation
from app.memory.mem0_service import MemoryType, remember
from app.observability.logging import get_logger
from app.security.auth import verify_api_key

log = get_logger(__name__)

router = APIRouter()

TTL = 3600


def envelope(message: str, data: dict | None = None, complete: bool = False,
             options: list[str] | None = None) -> dict:
    return {"message": message, "data": data or {}, "complete": complete,
            "options": options or []}


async def _save(state: QBState, project_id: str) -> dict:
    async with get_sessionmaker()() as db, db.begin():
        gq = GeneratedQuery(
            project_id=uuid.UUID(project_id),
            brand=state.brand,
            query_groups=state.query_groups,
            competitors=state.competitors,
        )
        db.add(gq)
        session_row = SessionRow(
            project_id=uuid.UUID(project_id),
            status="created",
            config={
                "brand": state.brand,
                "industry": state.industry,
                "query_groups": state.query_groups,
                "competitors": state.competitors,
            },
        )
        db.add(session_row)
        await db.flush()
        ids = {"generated_query_id": str(gq.id), "session_id": str(session_row.id)}

    with contextlib.suppress(Exception):  # profile memory is best-effort
        await remember(
            agent="query_builder", memory_type=MemoryType.PROFILE, project_id=project_id,
            content=(
                f"Query configuration saved for {state.brand} ({state.industry}): "
                f"{len(state.query_groups)} groups, competitors: {', '.join(state.competitors)}"
            ),
        )
    return ids


@router.websocket("/ws/query-builder")
async def query_builder_ws(
    ws: WebSocket,
    project_id: str = Query(...),
    api_key: str | None = Query(None),
    resume: str | None = Query(None),
) -> None:
    ok, reason = verify_api_key(api_key)
    if not ok:
        await ws.close(code=4403, reason=reason)
        return
    await ws.accept()

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    token = resume or uuid.uuid4().hex[:12]
    state = QBState()
    if resume:
        snapshot = await r.get(f"ws:qb:{resume}")
        if snapshot:
            state = QBState.model_validate_json(snapshot)

    await ws.send_json(
        envelope(
            "Welcome to query configuration. Which brand, company or product should we monitor?"
            if not resume else f"Resumed at sub-stage '{state.sub_stage}'. Where were we?",
            data={"sub_stage": state.sub_stage, "resume_token": token},
        )
    )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                incoming = json.loads(raw)
            except json.JSONDecodeError:
                incoming = {"message": raw}

            if incoming.get("action") == "save":
                if not state.brand or not state.query_groups:
                    await ws.send_json(envelope(
                        "I can't save yet — brand and at least one query group are required.",
                        data={"sub_stage": state.sub_stage},
                    ))
                    continue
                ids = await _save(state, project_id)
                await ws.send_json(envelope(
                    f"Saved. Session {ids['session_id']} is ready for collection.",
                    data={**ids, "config": state.model_dump(exclude={"history"})},
                    complete=True,
                ))
                break

            user_message = str(incoming.get("message", "")).strip()
            if not user_message:
                continue

            try:
                state, turn = await run_turn(state, user_message)
            except GuardrailViolation as gv:
                await ws.send_json(envelope(
                    "That message was blocked by input safety checks — please rephrase.",
                    data={"blocked": [f['check'] for f in gv.findings if f['verdict'] == 'blocked']},
                ))
                continue

            await r.set(f"ws:qb:{token}", state.model_dump_json(), ex=TTL)
            await ws.send_json(envelope(
                turn.message,
                data={
                    "sub_stage": state.sub_stage,
                    "brand": state.brand,
                    "industry": state.industry,
                    "query_groups": state.query_groups,
                    "competitors": state.competitors,
                    "resume_token": token,
                },
                options=turn.options,
            ))
    except WebSocketDisconnect:
        log.info("qb.disconnected", token=token, sub_stage=state.sub_stage)
    finally:
        with contextlib.suppress(Exception):
            await r.aclose()
