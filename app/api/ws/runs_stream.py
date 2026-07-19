"""/ws/runs/{run_id} — replay past events from a sequence number, then follow live."""

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.observability.logging import get_logger
from app.orchestration.events import channel_for, get_event_bus
from app.security.auth import verify_api_key

log = get_logger(__name__)

router = APIRouter()


def _same_origin(ws: WebSocket) -> bool:
    """Reject cross-site WebSocket handshakes (CSWSH): a malicious page must not
    open an authenticated socket on the strength of the browser's cookie."""
    origin = ws.headers.get("origin")
    if origin is None:
        return True  # non-browser client (no Origin header); relies on key auth
    try:
        from urllib.parse import urlparse

        return urlparse(origin).netloc == ws.headers.get("host")
    except Exception:
        return False


@router.websocket("/ws/runs/{run_id}")
async def run_stream(
    ws: WebSocket,
    run_id: uuid.UUID,
    from_seq: int = Query(0),
    api_key: str | None = Query(None),
) -> None:
    from app.security.auth import SESSION_COOKIE, valid_session

    if not _same_origin(ws):
        await ws.close(code=4403, reason="cross-origin websocket rejected")
        return
    # cookie session (browser console) or api_key query (non-browser clients)
    cookie_ok = await valid_session(ws.cookies.get(SESSION_COOKIE))
    key_ok = verify_api_key(api_key)[0] if api_key else False
    if not (cookie_ok or key_ok):
        await ws.close(code=4403, reason="authentication required")
        return
    await ws.accept()
    bus = get_event_bus()

    pubsub = bus.redis().pubsub()
    await pubsub.subscribe(channel_for(run_id))
    try:
        # subscribe BEFORE replay so nothing falls in the gap; dedupe by seq
        history = await bus.replay(run_id, from_seq=from_seq)
        last_seq = from_seq
        for event in history:
            await ws.send_json(event)
            last_seq = max(last_seq, event["seq"])

        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=25)
            if msg is None:
                # keepalive ping; also lets us notice a closed socket
                await ws.send_json({"event_type": "ping"})
                continue
            import json

            event = json.loads(msg["data"])
            if event.get("seq", 0) > last_seq:
                last_seq = event["seq"]
                await ws.send_json(event)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.warning("ws.runs_stream_error", run_id=str(run_id), error=str(exc))
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel_for(run_id))
            await pubsub.aclose()
