# Sub-project 6 — Production Deployment Runbook

Puts the always-on PR Intelligence backend on a real host so email/Teams polling,
the pipeline, memory, and the in-report chat run 24/7 — with dashboards auto-published
to Vercel. Recommended platform: **Render** (Docker + managed Postgres + Key Value),
plus **Neo4j AuraDB** for the graph store and **Vercel** for the static dashboards.

## Architecture

| Component | Where | Notes |
|---|---|---|
| API + workers + 5 s poll loop | Render **web service** (Docker, paid `standard`) | one process; RunManager tasks + inbound loop run in the FastAPI lifespan |
| Postgres (+ pgvector, artifacts, checkpointer) | Render **Postgres 16** | enable `vector` extension once |
| Redis (event bus, cache, sessions) | Render **Key Value** | `noeviction` |
| Neo4j (Mem0 graph) | **Neo4j AuraDB** (external) | free tier ok; `neo4j+s://` |
| teams-mcp OAuth token | Render **disk** at `/app/data/local` | survives deploys; self-refresh keeps it live |
| Dashboards + chat frontend | **Vercel** | auto-deployed per report (see below) |

`.env.production.example` is the full secrets checklist; `render.yaml` is the Blueprint.

## Step-by-step

1. **Neo4j AuraDB** — create a free instance, note `NEO4J_URI` / user / password.
2. **Render Blueprint** — New → Blueprint → point at this repo → it reads `deploy/render.yaml`
   and creates `prsol-api` + `prsol-postgres` + `prsol-redis` + the disk.
3. **Postgres pgvector** — in the Render PSQL shell: `CREATE EXTENSION IF NOT EXISTS vector;`
4. **Set secrets** (prsol-api → Environment) from `.env.production.example`. For `DATABASE_URL`
   paste the **Internal** Postgres URL but change the scheme to `postgresql+asyncpg://`.
   Set a **real** `AZURE_OPENAI_EMBED_DEPLOYMENT` (fixes the relevancy/RAG `DeploymentNotFound`)
   and a real `ANTHROPIC_API_KEY` (else everything falls back to Azure).
5. **Seed the teams token** — one-time: run `scripts/teams_auth.py` locally to produce
   `data/local/teams_token.json`, then upload it to the mounted disk (Render Shell:
   paste the JSON to `/app/data/local/teams_token.json`). The self-managed refresh
   (`teams_token_store.refresh`) keeps it alive from there.
6. **Deploy** — Render builds `docker/api.Dockerfile` and runs
   `alembic upgrade head && uvicorn …`. Health at `/health`.

## Validate after deploy

- `GET https://<service>.onrender.com/health` → `{"status":"ok"}` with db/redis/neo4j up.
- Logs show `channels.bootstrapped` + `inbound_started every=5`.
- Email `Monitor BeOne` from a stakeholder → run starts → gate emails arrive.
- `POST /runs` (X-API-Key) drives a run headless.

## Automated per-report Vercel deploy

With `VERCEL_TOKEN` set, the deliver stage publishes each finished dashboard to Vercel
and appends it to a per-project index (implemented in `app/services/vercel_publisher.py`,
wired into the pipeline `deliver` node). Each report gets a durable URL like
`https://prsol-<project>-<taskid>.vercel.app`; the index page lists all previous reports.
Without the token, delivery falls back to OneDrive links + local export (unchanged).

## Cost & gotchas

- The web service **must be paid/always-on** — free tier sleeps and the 5 s poll loop stops.
- Render's ephemeral FS means the teams token **needs the disk** (step 5), else it's lost on redeploy.
- `WATCHFILES_FORCE_POLLING=false` in prod (that flag is only for the local Windows bind mount).
- SearXNG is dropped in prod (`SEARXNG_URL=""`); the other 5 sources cover it.
- Scale-out later: split the poll loop / RunManager into a separate Render **worker** service
  sharing the same DB/Redis (the ContextVar + checkpointer design already supports it).
