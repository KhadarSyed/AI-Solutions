# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The **PR Intelligence Agent** — a LangGraph-orchestrated multi-agent platform for media monitoring and PR analytics, specified by `PR_Intelligence_Agent_Playbook_v3.docx` (extract text: it's a zip — `python -c` with zipfile + regex on `word/document.xml`). The build plan and all locked architecture decisions live in the session plan; the architecture summary is published as an artifact ("PR Intelligence Agent — Architecture").

This directory is its **own git repository** (nested inside the shared `C:/KhadarBasha/Projects` root — the parent repo shows it as one untracked dir; never run git from the parent).

## Commands

```bash
uv sync                      # install deps (Python 3.12, uv-managed)
docker compose up -d --build # full local stack (Rancher Desktop)
docker compose --profile sandbox --profile email up -d   # + sandbox & greenmail
uv run pytest -q             # tests
uv run ruff check .          # lint
uv run alembic upgrade head  # migrations (also runs in api entrypoint)
uv run alembic revision --autogenerate -m "msg"
langgraph dev                # LangGraph Studio (host, not compose)
curl http://localhost:8002/health
powershell scripts/autostart-install.ps1   # start stack at logon (uninstall script alongside)
```

Local ports (chosen to avoid sibling projects): **API 8002, Postgres 5434, Redis 6381, Neo4j 7475/7688, SearXNG 8083, greenmail 3025/3143**. All services use `restart: unless-stopped` — the stack survives reboots until explicitly `docker compose down`/`stop`.

## Architecture (the load-bearing decisions)

- **Orchestration**: LangGraph `StateGraph`s in `app/orchestration/graphs/` with a Postgres checkpointer; human gates are `interrupt()`s. **Two channel-delivered gates**: after collection (CSV → user's channel, await consent) and after tagging (tagged CSV → channel, await approval). Pydantic AI agents (in `app/agents/`) run inside nodes.
- **The only LLM path** is `app/llm_gateway/` — model factory (`LLM_PROVIDER=gpt|claude`), `FallbackModel` cross-provider failover, per-model output clamps (gpt-4o 16384 vs sonnet 32000), Redis cache (temp=0 only), cost rows to `llm_calls`. Agents must not import provider SDKs directly.
- **Guardrails are hybrid**: deterministic checks (`app/guardrails/input.py|output.py`) always on; NeMo rails only on user-originated input and stakeholder-facing output — never in tagging batch loops.
- **Memory**: Mem0 (`app/memory/mem0_service.py`) with pgvector vector store + Neo4j graph store; taxonomy helpers (episodic/semantic/procedural/feedback/research). Structured corrections stay in the `correction_events` SQL table and are distilled into Mem0 each run. Article RAG embeddings are separate (`article_embeddings`, 1536-dim, always Azure `text-embedding-3-small` — Anthropic has no embeddings API).
- **Retrieval**: pgvector top-50 → FlashRank local rerank → top-10 → grounded generation with citations; approved-only filter is mandatory for stakeholder-facing answers.
- **Artifacts** (source_file → tagged_file → charts_data_file) live in **PostgreSQL** (JSONB/BYTEA) behind `app/artifacts/` ABC with S3-style keys; sessions store key strings. Tagged articles are a JSONB artifact, NOT a table — review mutations use `SELECT … FOR UPDATE` read-modify-write in one transaction (edit + correction log + cache invalidation + embedding flag sync).
- **Execution layer**: every node transition/token/memory write/guardrail event goes through the EventBus → `run_events` (append-only) + Redis pub/sub → `/ws/runs/{run_id}` (replay + live). RunManager: concurrent runs, per-session mutex, pause/resume/restart, heartbeat + startup recovery sweep.
- **Channels**: `app/channels/` ChannelAdapter ABC — web (WS), email (greenmail/IMAP locally), Teams chat/channel + Graph mail via the user's **production teams-mcp server** (MCP client; needs a durable OAuth token — see risks in plan). HITL notifications always go to the run's **origin channel**.
- **Migrations**: Alembic is the ONLY DDL path. Never hand-edit the DB, never add init SQL to compose. The LangGraph checkpointer's own tables are excluded from autogenerate.

## Gotchas

- `uvicorn --reload` needs `WATCHFILES_FORCE_POLLING=true` across the Windows bind mount (already set in compose); fallback `docker compose restart api`.
- Body extraction uses **trafilatura/Scrapling** — newspaper3k is unmaintained and breaks on py3.12.
- Connectors are enabled by their env keys being non-empty; every connector must normalize to the 9-field record (publisher, title, content, domain, date, url, author, country default "all", language) and declare `capabilities` for source-side filters.
- LangSmith/Logfire are optional (empty key = disabled); never block startup on tracing.
- Mem0 `add()` runs LLM extraction — batch memory writes at stage boundaries, cap per run.
