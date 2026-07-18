"""Self-healing ladder — when an agent/node fails, it looks for its own cure.

Ladder:
  1. Known-fix registry — procedural memory lookup by error signature.
  2. Web-search healing — DuckDuckGo (ddgs) the error, read top fixes, ask the
     gateway for a structured RemediationPlan, apply supported actions.
  3. Browser-assisted (SELF_HEAL_BROWSER=1) — drive a browser to an assistant,
     scrape the suggested fix, follow it. Implemented in Phase C alongside the
     containerized Playwright runtime.

Every attempt is reported through the event hook and successful remedies are
written back to procedural memory so step 1 hits next time.
"""

import contextlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.llm_gateway.guarded import GuardedAgent
from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger

log = get_logger(__name__)

HealEventHook = Callable[[str, dict], Awaitable[None]]
_hook: HealEventHook | None = None


def set_heal_event_hook(hook: HealEventHook) -> None:
    global _hook
    _hook = hook


async def _emit(event: str, payload: dict) -> None:
    if _hook is not None:
        with contextlib.suppress(Exception):
            await _hook(event, payload)


class RemedyAction(StrEnum):
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    IMPERSONATION_PROFILE = "impersonation_profile"
    ADJUST_WAIT_STRATEGY = "adjust_wait_strategy"
    ROTATE_SOURCE = "rotate_source"
    SKIP_ITEM = "skip_item"


class RemediationPlan(BaseModel):
    actions: list[RemedyAction] = Field(description="Ordered remediation actions to apply")
    rationale: str = Field(description="One-sentence reason grounded in the fixes read")


@dataclass
class HealResult:
    healed: bool
    source: str            # known_fix | web_search | browser | none
    plan: RemediationPlan | None = None
    detail: str = ""
    attempts: list[str] = field(default_factory=list)


def error_signature(exc_text: str) -> str:
    """Stable signature: exception class + salient tokens, no addresses/ids."""
    head = exc_text.strip().splitlines()[-1][:200]
    return re.sub(r"0x[0-9a-f]+|\d{4,}", "*", head)


async def _search_fixes(query: str, max_results: int = 4) -> list[str]:
    from ddgs import DDGS

    texts: list[str] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            snippet = f"{r.get('title', '')} — {r.get('body', '')}"
            texts.append(snippet[:500])
    return texts


async def attempt_heal(
    *,
    agent: str,
    project_id: str,
    error_text: str,
    apply: dict[RemedyAction, Callable[[], Awaitable[bool]]],
) -> HealResult:
    """Run the ladder. `apply` maps supported actions to callables that apply the
    remedy and return True when a retry afterwards succeeded."""
    if not get_settings().self_heal_enabled:
        return HealResult(healed=False, source="none", detail="self-heal disabled")

    sig = error_signature(error_text)
    result = HealResult(healed=False, source="none")
    await _emit("heal_started", {"agent": agent, "signature": sig})

    # 1 — known fix from procedural memory
    known = await recall(
        agent=agent, query=f"remedy for: {sig}", project_id=project_id,
        memory_type=MemoryType.PROCEDURAL, limit=3,
    )
    for hit in known:
        text = str(hit.get("memory", ""))
        for action in RemedyAction:
            if action.value in text and action in apply:
                result.attempts.append(f"known_fix:{action.value}")
                await _emit("heal_source", {"agent": agent, "source": "known_fix", "action": action.value})
                if await apply[action]():
                    result.healed, result.source = True, "known_fix"
                    result.detail = f"procedural memory remedy {action.value}"
                    await _emit("heal_result", {"agent": agent, "healed": True, "source": "known_fix"})
                    return result

    # 2 — web-search healing
    try:
        fixes = await _search_fixes(f"{sig} fix python")
    except Exception as exc:
        fixes = []
        result.attempts.append(f"web_search_failed:{exc}")

    if fixes:
        await _emit("heal_source", {"agent": agent, "source": "web_search", "fixes_read": len(fixes)})
        planner = GuardedAgent(
            purpose="self_heal_plan",
            stage="self_heal",
            system_prompt=(
                "You are a remediation planner. Given an error and fix notes from the web, "
                "choose ONLY from the allowed actions the ordered subset most likely to recover."
            ),
            output_type=RemediationPlan,
            temperature=0.0,
            cacheable=True,
        )
        try:
            plan = await planner.run(
                f"Error:\n{error_text[:1500]}\n\nFix notes:\n" + "\n---\n".join(fixes)
            )
            result.plan = plan
            for action in plan.actions:
                if action not in apply:
                    continue
                result.attempts.append(f"web_search:{action.value}")
                if await apply[action]():
                    result.healed, result.source = True, "web_search"
                    result.detail = plan.rationale
                    await remember(
                        agent=agent, memory_type=MemoryType.PROCEDURAL, project_id=project_id,
                        content=f"Remedy for `{sig}`: apply {action.value}. {plan.rationale}",
                        metadata={"signature": sig, "action": action.value},
                    )
                    await _emit("heal_result", {"agent": agent, "healed": True, "source": "web_search",
                                                "action": action.value})
                    return result
        except Exception as exc:
            result.attempts.append(f"planner_failed:{exc}")

    # 3 — browser-assisted (flagged; lands with the Phase C browser runtime)
    if get_settings().self_heal_browser:
        await _emit("heal_source", {"agent": agent, "source": "browser", "status": "not_available_yet"})
        result.attempts.append("browser:pending_phase_c")

    await _emit("heal_result", {"agent": agent, "healed": False, "attempts": result.attempts})
    return result
