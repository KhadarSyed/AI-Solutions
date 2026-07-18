"""Wired pipeline graph: real services end-to-end (offline, zero-article session),
interrupt at both gates, resume on approval, durable Postgres checkpoints."""

import uuid

from langgraph.types import Command

from app.orchestration.checkpoint import checkpointer, setup_checkpointer_tables
from app.orchestration.graphs.pipeline_graph import build_pipeline_graph


async def test_gates_interrupt_and_resume_to_completion(blank_session):
    await setup_checkpointer_tables()
    async with checkpointer() as saver:
        graph = build_pipeline_graph(saver)
        cfg = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
        init = {**blank_session, "origin_channel": "web"}

        result = await graph.ainvoke(init, cfg)
        assert "__interrupt__" in result
        gate1 = result["__interrupt__"][0].value
        assert gate1["gate"] == 1
        assert gate1["csv_key"].endswith("gate1_review.csv")

        result = await graph.ainvoke(Command(resume={"decision": "approved"}), cfg)
        assert result["__interrupt__"][0].value["gate"] == 2

        result = await graph.ainvoke(Command(resume={"decision": "approved"}), cfg)
        assert "__interrupt__" not in result
        assert result["gate1_decision"] == "approved"
        assert result["gate2_decision"] == "approved"
        assert result["charts_data_file_key"].endswith("charts_data_file.json")


async def test_gate1_changes_loops_back_to_collect(blank_session):
    async with checkpointer() as saver:
        graph = build_pipeline_graph(saver)
        cfg = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
        await graph.ainvoke({**blank_session, "origin_channel": "web"}, cfg)

        result = await graph.ainvoke(
            Command(resume={"decision": "changes", "feedback": "add competitor Carrier"}), cfg
        )
        assert result["__interrupt__"][0].value["gate"] == 1
