"""Graph state models — the working memory of a pipeline run."""

from typing import Annotated, Any, TypedDict


def _merge_dict(left: dict, right: dict) -> dict:
    return {**left, **right}


class PipelineState(TypedDict, total=False):
    # identity
    run_id: str
    task_id: str                 # human-friendly BRAND-YYYYMMDD-NNN, shown in subjects
    session_id: str
    project_id: str
    origin_channel: str          # web | email | teams_chat | teams_channel | scheduler
    origin_address: dict         # conversation/thread/message addressing

    # configuration snapshot (from Stage 1 / session.config)
    brand: str
    query_groups: list[dict]
    competitors: list[str]

    # stage progress
    raw_count: int
    unique_count: int
    enriched_count: int
    tagged_count: int
    approved_count: int
    monitoring_count: int         # rows kept for monitoring (feed the final dashboard)

    # gate outcomes ("approved" | "changes" | None while waiting)
    plan_decision: str | None
    plan_feedback: str
    gate1_decision: str | None
    gate1_feedback: str
    gate2_decision: str | None
    gate2_feedback: str
    tagging_additions: list[str]   # user-requested extra data points for this run

    # artifact pointers
    source_file_key: str
    tagged_file_key: str
    charts_data_file_key: str
    dashboard_url: str            # Vercel-hosted report URL (when auto-publish is on)

    # bookkeeping
    notes: Annotated[dict[str, Any], _merge_dict]
    error: str | None
