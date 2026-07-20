import pytest

from app.orchestration import run_manager as rm


@pytest.mark.asyncio
async def test_try_heal_runs_ladder_and_retries(monkeypatch):
    mgr = rm.RunManager()
    calls = {"stream": 0}

    async def fake_stream(run_id, thread_id, gname, gin):
        calls["stream"] += 1  # the retry re-runs the graph from checkpoint

    async def instant(*a, **k):
        return None

    monkeypatch.setattr(mgr, "_stream_graph", fake_stream)
    monkeypatch.setattr(rm.asyncio, "sleep", instant)

    async def fake_attempt_heal(*, agent, project_id, error_text, apply):
        from app.orchestration.self_heal import HealResult, RemedyAction

        ok = await apply[RemedyAction.RETRY_WITH_BACKOFF]()  # exercise the retry
        return HealResult(healed=ok, source="known_fix" if ok else "none")

    monkeypatch.setattr("app.orchestration.self_heal.attempt_heal", fake_attempt_heal)

    healed = await mgr._try_heal("r1", "t1", "pipeline", {"project_id": "p1"},
                                 RuntimeError("boom"))
    assert healed is True
    assert calls["stream"] == 1


@pytest.mark.asyncio
async def test_try_heal_returns_false_when_retry_fails(monkeypatch):
    mgr = rm.RunManager()

    async def failing_stream(run_id, thread_id, gname, gin):
        raise RuntimeError("still broken")

    async def instant(*a, **k):
        return None

    monkeypatch.setattr(mgr, "_stream_graph", failing_stream)
    monkeypatch.setattr(rm.asyncio, "sleep", instant)

    async def fake_attempt_heal(*, agent, project_id, error_text, apply):
        from app.orchestration.self_heal import HealResult, RemedyAction

        ok = await apply[RemedyAction.RETRY_WITH_BACKOFF]()
        return HealResult(healed=ok, source="none")

    monkeypatch.setattr("app.orchestration.self_heal.attempt_heal", fake_attempt_heal)
    healed = await mgr._try_heal("r1", "t1", "pipeline", {"project_id": "p1"},
                                 RuntimeError("boom"))
    assert healed is False
