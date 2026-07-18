"""Inbound router / email agent — maps channel messages to one of:

  1. a pending gate on a run awaiting_human → classify approve/changes → resume
  2. a post-pipeline query → Brain-routed grounded reply on the same channel
  3. a change request → re-run affected stages via the RunManager

Runs for email and Teams alike; the origin channel is honored on every reply."""

import contextlib
import re
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.channels.base import ChannelInbound, OutboundMessage
from app.db.base import get_sessionmaker
from app.db.models import Project, Run
from app.db.models import Session as SessionRow
from app.llm_gateway.guarded import GuardedAgent
from app.observability.logging import get_logger

log = get_logger(__name__)

_APPROVE_RE = re.compile(r"\b(approve|approved|looks good|go ahead|proceed|yes)\b", re.I)
_CHANGES_RE = re.compile(r"\b(change|changes|revise|instead|add|remove|different|redo)\b", re.I)


class InboundIntent(BaseModel):
    kind: str = Field(description="gate_decision | question | change_request | report_request")
    gate_decision: str = Field(default="", description="approved|changes when kind=gate_decision")


async def _adapter_for(channel: str):
    from app.channels.email_imap import EmailAdapter
    from app.channels.teams_mcp import TeamsMcpAdapter

    if channel.startswith("teams"):
        return TeamsMcpAdapter()
    return EmailAdapter()


async def _match_project(inbound: ChannelInbound) -> Project | None:
    async with get_sessionmaker()() as db:
        projects = (await db.execute(select(Project))).scalars().all()
    for p in projects:
        emails = [e.lower() for e in (p.stakeholder_emails or [])]
        if inbound.sender.lower() in emails:
            return p
        if p.brand_name and p.brand_name.lower() in (inbound.subject + inbound.text).lower():
            return p
    return projects[0] if len(projects) == 1 else None


async def _pending_gate_run(project_id: str) -> Run | None:
    async with get_sessionmaker()() as db:
        return (
            await db.execute(
                select(Run).where(Run.status == "awaiting_human",
                                  Run.session_id.in_(
                                      select(SessionRow.id).where(
                                          SessionRow.project_id == uuid.UUID(project_id))
                                  ))
                .order_by(Run.created_at.desc())
            )
        ).scalars().first()


async def _classify(inbound: ChannelInbound, has_pending_gate: bool) -> InboundIntent:
    # cheap deterministic path for clear gate replies
    if has_pending_gate:
        if _APPROVE_RE.search(inbound.text) and not _CHANGES_RE.search(inbound.text):
            return InboundIntent(kind="gate_decision", gate_decision="approved")
        if _CHANGES_RE.search(inbound.text):
            return InboundIntent(kind="gate_decision", gate_decision="changes")
    agent = GuardedAgent(
        purpose="inbound_intent", stage="email_agent",
        system_prompt=(
            "Classify a stakeholder message. kind is one of: gate_decision (they are "
            "approving or requesting changes to a pending review), question (asking about "
            "coverage), change_request (asking to change the analysis: new competitor, date "
            "range, sections), report_request (wants the report). If gate_decision, set "
            "gate_decision to approved or changes."
        ),
        output_type=InboundIntent, temperature=0.0, user_originated_input=True,
    )
    try:
        return await agent.run(f"Subject: {inbound.subject}\n\n{inbound.text}")
    except Exception:
        return InboundIntent(kind="question")


async def _answer_query(project_id: str, run: Run | None, inbound: ChannelInbound) -> str:
    """Post-pipeline Q&A grounded in the session corpus via the data agent flow."""
    from app.agents.data_agent import _question_flow

    session_id = str(run.session_id) if run and run.session_id else None
    if session_id is None:
        async with get_sessionmaker()() as db:
            session_id = str((
                await db.execute(select(SessionRow.id)
                                 .where(SessionRow.project_id == uuid.UUID(project_id),
                                        SessionRow.status == "charts_ready")
                                 .order_by(SessionRow.created_at.desc()))
            ).scalars().first() or "")
    if not session_id:
        return "I don't have an analyzed session for this project yet."

    parts: list[str] = []
    async for ev in _question_flow(project_id, session_id, inbound.text,
                                   request_id=uuid.uuid4().hex[:16]):
        if ev["event"] == "answer":
            parts.append(ev["data"]["text"])
    return "\n\n".join(parts) or "I couldn't find grounded coverage for that question."


async def handle_inbound(inbound: ChannelInbound) -> dict:
    project = await _match_project(inbound)
    if project is None:
        log.info("inbound.unmatched", sender=inbound.sender)
        return {"handled": False, "reason": "no matching project"}
    project_id = str(project.id)

    run = await _pending_gate_run(project_id)
    intent = await _classify(inbound, has_pending_gate=run is not None)
    adapter = await _adapter_for(inbound.channel)

    if intent.kind == "gate_decision" and run is not None:
        from app.orchestration.run_manager import get_run_manager

        decision = intent.gate_decision or "approved"
        await get_run_manager().resume(
            str(run.id), {"decision": decision, "feedback": inbound.text}
        )
        with contextlib.suppress(Exception):
            await adapter.send(inbound.address, OutboundMessage(
                subject=f"Re: {inbound.subject}",
                text=(f"Thanks — recorded your '{decision}'. "
                      + ("Continuing the pipeline now." if decision == "approved"
                         else "Re-running with your changes.")),
            ))
        return {"handled": True, "action": f"gate_{decision}", "run_id": str(run.id)}

    # question / report / change → grounded reply (change-request re-run is scoped to
    # the pipeline's gate-changes loop; a bare change with no pending gate is answered)
    answer = await _answer_query(project_id, run, inbound)
    with contextlib.suppress(Exception):
        await adapter.send(inbound.address, OutboundMessage(
            subject=f"Re: {inbound.subject}", text=answer,
        ))
    return {"handled": True, "action": intent.kind}
