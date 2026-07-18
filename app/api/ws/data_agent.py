"""WS /ws/agent — Stage 6 interactive data agent stream.

Streams typed events: start → intent_class → (chart: plan→code→[code_error]→chart)
| (question: intent→retrieval/web→answer) → complete."""

import contextlib
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.agents.data_agent import handle_message
from app.db.base import get_sessionmaker
from app.db.models import Session as SessionRow
from app.observability.logging import get_logger
from app.security.auth import verify_api_key

log = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/agent")
async def data_agent_ws(
    ws: WebSocket,
    session_id: str = Query(...),
    api_key: str | None = Query(None),
) -> None:
    ok, reason = verify_api_key(api_key)
    if not ok:
        await ws.close(code=4403, reason=reason)
        return

    async with get_sessionmaker()() as db:
        row = await db.get(SessionRow, uuid.UUID(session_id))
    if row is None:
        await ws.close(code=4404, reason="session not found")
        return
    project_id = str(row.project_id)

    await ws.accept()
    try:
        while True:
            message = (await ws.receive_text()).strip()
            if not message:
                continue
            request_id = uuid.uuid4().hex[:16]
            async for event in handle_message(
                project_id=project_id, session_id=session_id,
                message=message, request_id=request_id,
            ):
                await ws.send_json(event)
    except WebSocketDisconnect:
        log.info("data_agent.disconnected", session=session_id)
    except Exception as exc:
        log.warning("data_agent.error", session=session_id, error=str(exc)[:200])
        with contextlib.suppress(Exception):
            await ws.send_json({"event": "error", "data": {"message": str(exc)[:300]}})
