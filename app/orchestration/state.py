"""Graph state models — the working memory of a pipeline run."""

from typing import Annotated, Any, TypedDict


def _merge_dict(left: dict, right: dict) -> dict:
    return {**left, **right}


class PipelineState(TypedDict, total=False):
    # identity
    run_id: str
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

    # gate outcomes ("approved" | "changes" | None while waiting)
    gate1_decision: str | None
    gate1_feedback: str
    gate2_decision: str | None
    gate2_feedback: str

    # artifact pointers
    source_file_key: str
    tagged_file_key: str
    charts_data_file_key: str

    # bookkeeping
    notes: Annotated[dict[str, Any], _merge_dict]
    error: str | None
