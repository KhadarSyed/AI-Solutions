"""In-report chat — answers questions about a specific report. The Brain router
(inside the data agent) picks RAG over this report's approved corpus, a direct LLM
reply, or a web lookup. Auth is a per-report HMAC token embedded in the dashboard
(never the admin key); interaction is limited to 3 days from report generation."""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.base import get_sessionmaker
from app.db.models import Session as SessionRow
from app.observability.logging import get_logger
from app.security.auth import verify_chat_token

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])
CHAT_WINDOW_DAYS = 3


class ChatBody(BaseModel):
    question: str
    token: str


@router.post("/{session_id}")
async def ask(session_id: str, body: ChatBody) -> dict:
    if not verify_chat_token(session_id, body.token):
        raise HTTPException(403, "invalid chat token")
    if not (body.question or "").strip():
        raise HTTPException(422, "empty question")
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(404, "session not found") from exc

    async with get_sessionmaker()() as db:
        row = await db.get(SessionRow, sid)
    if row is None:
        raise HTTPException(404, "session not found")

    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if datetime.now(UTC) - created >= timedelta(days=CHAT_WINDOW_DAYS):
        raise HTTPException(410, "this report's chat window (3 days) has closed")

    from app.agents.data_agent import _question_flow

    parts: list[str] = []
    async for ev in _question_flow(str(row.project_id), session_id, body.question,
                                   request_id=uuid.uuid4().hex[:16]):
        if ev.get("event") == "answer":
            parts.append(ev["data"]["text"])
    answer = "\n\n".join(parts) or "I couldn't find grounded coverage for that question."
    return {"answer": answer, "timestamp": datetime.now(UTC).isoformat()}
