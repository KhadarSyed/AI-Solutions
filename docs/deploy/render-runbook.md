# Render production runbook — bring the API's data stores online

**Why this exists:** the live `prsol-api.onrender.com` app responds (`/docs`, `/openapi.json`,
`/static/*` all 200), but its **database, Redis, and Neo4j are not connected** — so `/health`
and every data route (including the in-report `/chat`) hang until timeout. That is the root
cause of the "chat error" on Vercel-hosted reports: the report calls back to this API, but the
API can't reach its DB.

Code is ready (`deploy/render.yaml` Blueprint + hardened `/health` that now reports each store's
status in ~5s instead of hanging). The remaining steps are **dashboard actions only you can do**.

## 1. Sync the Blueprint

In Render → **Blueprints → New/Sync from repo** → pick `deploy/render.yaml` on `feat/finalize`
(or merge to `main` first). It provisions: `prsol-api` (Docker web, standard plan — never
sleeps), `prsol-postgres` (managed PG16), `prsol-redis` (managed Key Value), and a 1 GB disk at
`/app/data/local`. `REDIS_URL` and `ADMIN_API_KEY` wire automatically.

## 2. Set DATABASE_URL with the async driver

Render's Postgres gives a URL starting `postgresql://…`. The app needs the **asyncpg** driver.
Copy the **Internal Database URL** and change the scheme:

```
postgresql+asyncpg://USER:PASSWORD@HOST/DBNAME
```

Paste that as the `DATABASE_URL` env var on `prsol-api`. (pgvector is created automatically by
the first migration — `CREATE EXTENSION IF NOT EXISTS vector` — no manual step needed on PG16.)

## 3. Provision Neo4j (Render has none)

Create a free **Neo4j AuraDB** instance → set on `prsol-api`:
`NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.
(If you skip this, memory-graph features degrade but the app still runs — `/health` will show
`neo4j: down`, which is now non-fatal.)

## 4. Enter the secrets (all `sync:false` vars)

`ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_EMBED_DEPLOYMENT` (only if `EMBEDDING_BACKEND=azure`; default is local ONNX),
`SERPAPI_API_KEY`, `TAVILY_API_KEY`, `APIFY_TOKEN`, `PEXELS_API_KEY`, `XPOZ_API_KEY`,
`CC_STACK_EMAIL`, `VERCEL_TOKEN`, `LOGFIRE_TOKEN`. Everything else is baked in the Blueprint.

`PUBLIC_API_BASE` is already baked to `https://prsol-api.onrender.com` — this is what makes the
deployed report's chat + drag-to-move reach the backend. Change it only if you rename the service.

## 5. Deploy, then verify with /health

Migrations run on start (`alembic upgrade head`). After deploy:

```
curl https://prsol-api.onrender.com/health
# want: {"status":"ok","db":"up","redis":"up","neo4j":"up"}
```

If any store shows `down: timeout` or `down: <Error>`, fix that connection string — `/health`
now names the culprit instead of hanging.

## 6. Seed the teams-mcp OAuth token (one time)

The disk persists `/app/data/local/teams_token.json`. Do one interactive sign-in so the durable
refresh token lands on the disk; it then survives deploys (the poller runs unattended after that).

## 7. Reports built here → chat works

The in-report chat answers from the session's corpus **on the backend that built the report**.
Once the pipeline runs on Render (email/Teams-triggered), each new report's data lives in Render
Postgres, so its deployed chat resolves. Reports built on your **local** machine can't be
chat-served from Render (Render's DB doesn't hold that data) — rebuild them here, or use a public
tunnel to your local API for testing.

## Note on the search fleet in prod

`GOOGLE_SEARCH_ENABLED` and `BROWSER_MCP_ENABLED` are **off** in the Blueprint: the Browser-MCP
Google path spawns Chromium per query and Google blocks automated search anyway. Google News RSS
(free, country-aware) + SerpAPI (your key) carry Google coverage. Flip either to `true` if you
want the browser paths and the service has headroom.
