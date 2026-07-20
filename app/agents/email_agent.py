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
    kind: str = Field(
        description="start_run | gate_decision | question | change_request | report_request"
    )
    gate_decision: str = Field(default="", description="approved|changes when kind=gate_decision")
    brand: str = Field(default="", description="brand to monitor when kind=start_run "
                       "(e.g. BeOne, Trane, Otsuka)")


_START_RE = re.compile(r"\b(start|run|begin|kick off|monitor|track|launch)\b", re.I)
_BRANDS = ("beone", "trane", "otsuka")

# subject gating — the agent acts only on proper-subject mail, never the whole inbox
_TASK_TAG = re.compile(r"\[[A-Z0-9]+-\d{8}-\d{3}\]")


def subject_allowed(subject: str, brands: list[str]) -> bool:
    """True if the subject is a start command for a known brand, or a reply
    carrying a Task-ID tag. Everything else is ignored."""
    s = subject or ""
    if _TASK_TAG.search(s):
        return True
    low = s.lower()
    return bool(_START_RE.search(low) and any(b.lower() in low for b in brands if b))


def _parse_competitor_override(text: str) -> list[str] | None:
    """Extract an explicit competitor list from the message, e.g. 'competitors: A, B'."""
    m = re.search(r"competitors?\s*[:\-]\s*(.+)", text or "", re.I)
    if not m:
        return None
    return [c.strip() for c in re.split(r"[,;]", m.group(1)) if c.strip()][:5]


async def _all_brands() -> list[str]:
    async with get_sessionmaker()() as db:
        return [p.brand_name for p in (await db.execute(select(Project))).scalars().all()]


async def _adapter_for(channel: str):
    from app.channels.email_imap import EmailAdapter
    from app.channels.teams_mcp import TeamsMcpAdapter

    if channel.startswith("teams"):
        return TeamsMcpAdapter()
    # email: send from the real mailbox via Graph when teams-mcp is available;
    # greenmail SMTP only as the offline fallback
    teams = TeamsMcpAdapter()
    if channel == "email" and teams.enabled():
        return teams
    return EmailAdapter()


def _sender_candidates(sender: str) -> set[str]:
    """Normalize an inbound sender to candidate emails. Teams B2B guests arrive
    as a mangled UPN like `khadar.syed_infovision.com#EXT#@tenant.onmicrosoft.com`
    — recover the home email `khadar.syed@infovision.com`."""
    s = sender.strip().lower()
    out = {s}
    if "#ext#" in s:
        local_domain = s.split("#ext#")[0]          # khadar.syed_infovision.com
        at = local_domain.rfind("_")
        if at != -1:
            out.add(local_domain[:at] + "@" + local_domain[at + 1:])
    return {c for c in out if "@" in c}


async def _match_project(inbound: ChannelInbound) -> tuple[Project | None, bool]:
    """Returns (project, sender_verified). sender_verified is True only when the
    inbound sender maps to a registered stakeholder — the sole basis for any
    state-mutating action. Brand-name-in-subject is NOT authorization (the From
    field is spoofable); no single-project fail-open."""
    async with get_sessionmaker()() as db:
        projects = (await db.execute(select(Project))).scalars().all()
    candidates = _sender_candidates(inbound.sender)
    for p in projects:
        stakeholders = {e.lower() for e in (p.stakeholder_emails or [])}
        if candidates & stakeholders:
            return p, True
    return None, False


async def _pending_gate_run(project_id: str, task_id: str | None = None) -> Run | None:
    async with get_sessionmaker()() as db:
        q = select(Run).where(
            Run.status == "awaiting_human",
            Run.session_id.in_(
                select(SessionRow.id).where(SessionRow.project_id == uuid.UUID(project_id))),
        )
        if task_id:   # match the exact task when the reply carries its [TASK-ID]
            q = q.where(Run.origin_address["task_id"].astext == task_id)
        return (await db.execute(q.order_by(Run.created_at.desc()))).scalars().first()


def _detect_brand(text: str) -> str:
    low = text.lower()
    for b in _BRANDS:
        if b in low:
            return {"beone": "BeOne", "trane": "Trane", "otsuka": "Otsuka"}[b]
    return ""


async def _classify(inbound: ChannelInbound, has_pending_gate: bool) -> InboundIntent:
    text = f"{inbound.subject}\n{inbound.text}"
    # cheap deterministic path for clear gate replies
    if has_pending_gate:
        if _APPROVE_RE.search(inbound.text) and not _CHANGES_RE.search(inbound.text):
            return InboundIntent(kind="gate_decision", gate_decision="approved")
        if _CHANGES_RE.search(inbound.text):
            return InboundIntent(kind="gate_decision", gate_decision="changes")
    # deterministic start-run detection (no pending gate)
    if not has_pending_gate and _START_RE.search(text):
        brand = _detect_brand(text)
        if brand:
            return InboundIntent(kind="start_run", brand=brand)
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


async def _start_run(project: Project, brand: str, inbound: ChannelInbound) -> dict:
    """Create a session for the brand and launch the pipeline on the origin channel,
    so both human gates come back to wherever the request arrived."""
    from app.agents.competitor_agent import resolve_competitors
    from app.db.models import GeneratedQuery
    from app.orchestration.run_manager import get_run_manager
    from app.orchestration.task_id import make_task_id
    from app.services.query_plan import build_query_plan

    brand = brand or project.brand_name
    task_id = await make_task_id(brand)
    override = _parse_competitor_override(inbound.text)   # user-named competitors win
    competitors = await resolve_competitors(brand, str(project.id), override=override)
    query_groups = build_query_plan(brand, competitors, project.industry)
    async with get_sessionmaker()() as db, db.begin():
        gq = GeneratedQuery(project_id=project.id, brand=brand,
                            query_groups=query_groups, competitors=competitors)
        db.add(gq)
        row = SessionRow(project_id=project.id,
                         config={"brand": brand, "query_groups": query_groups,
                                 "competitors": competitors})
        db.add(row)
        await db.flush()
        sid = str(row.id)

    run_id = await get_run_manager().start(
        graph_name="pipeline",
        input_state={"project_id": str(project.id), "session_id": sid,
                     "brand": brand, "query_groups": query_groups,
                     "competitors": competitors, "task_id": task_id},
        session_id=sid,
        origin_channel=inbound.channel,
        origin_address={**(inbound.address or {}), "task_id": task_id},
    )
    log.info("inbound.start_run", brand=brand, channel=inbound.channel,
             run_id=run_id, task_id=task_id)
    return {"run_id": run_id, "session_id": sid, "brand": brand, "task_id": task_id}


async def handle_inbound(inbound: ChannelInbound) -> dict:
    # Unverified senders are never authoritative — the From field is spoofable.
    project, sender_verified = await _match_project(inbound)
    if project is None or not sender_verified:
        log.info("inbound.unverified", sender=inbound.sender)
        return {"handled": False, "reason": "sender is not a registered stakeholder"}

    # subject gating — never act on arbitrary inbox mail (email channel only)
    if inbound.channel == "email" and not subject_allowed(inbound.subject, await _all_brands()):
        log.info("inbound.subject_unmatched", subject=(inbound.subject or "")[:80])
        return {"handled": False, "reason": "subject not recognized for this system"}
    project_id = str(project.id)

    from app.orchestration.task_id import extract_task_id

    tid = extract_task_id(inbound.subject) or extract_task_id(inbound.text)
    run = await _pending_gate_run(project_id, task_id=tid)
    intent = await _classify(inbound, has_pending_gate=run is not None)
    adapter = await _adapter_for(inbound.channel)

    if intent.kind == "start_run" and run is None:
        result = await _start_run(project, intent.brand, inbound)
        tid = result["task_id"]
        with contextlib.suppress(Exception):
            await adapter.send(inbound.address, OutboundMessage(
                subject=f"[{tid}] {intent.brand} Monitoring — started",
                text=(f"Task {tid} started for {intent.brand}. The WebSearch Agent is "
                      "collecting coverage now; I'll email the collected articles here for "
                      f"your review shortly. Keep [{tid}] in the subject on any reply."),
            ))
        return {"handled": True, "action": "start_run", **result}

    if intent.kind == "gate_decision" and run is not None:
        from app.orchestration.run_manager import get_run_manager
        from app.security.auth import token_in_text

        # A gate reply must carry the per-run token from the original notification.
        thread_text = f"{inbound.subject}\n{inbound.text}"
        if not token_in_text(str(run.id), thread_text):
            log.warning("inbound.missing_resume_token", run_id=str(run.id),
                        sender=inbound.sender)
            with contextlib.suppress(Exception):
                await adapter.send(inbound.address, OutboundMessage(
                    subject=f"Re: {inbound.subject}",
                    text=("I couldn't verify this reply against the pending review "
                          "(missing the reference code from the original message). "
                          "Please reply to that message so the reference is preserved."),
                ))
            return {"handled": False, "reason": "resume token missing/invalid"}

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
