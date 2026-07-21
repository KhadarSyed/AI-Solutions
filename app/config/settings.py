from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    app_env: Literal["dev", "test", "prod"] = "dev"
    api_port: int = 8002
    admin_api_key: str = "change-me-admin-key"

    # Storage
    database_url: str = "postgresql+asyncpg://prsol:prsol@localhost:5434/prsol"
    redis_url: str = "redis://localhost:6381/0"
    neo4j_uri: str = "bolt://localhost:7688"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "prsolneo4j"
    artifact_backend: Literal["postgres", "s3"] = "postgres"

    # Dashboard rendering
    dashboard_asset_mode: Literal["embed", "cdn"] = "embed"
    vercel_token: str = ""          # set → each finished report auto-publishes to Vercel
    # Where the in-report chat calls back. Empty → the widget uses window.location.origin
    # (a dashboard served from the API is then same-origin and works with no config).
    chat_api_base: str = ""
    public_api_base: str = ""                       # public HTTPS base for deployed reports
    # always-CC these on every task email (comma-separated), in addition to any trigger/reply CC
    cc_stack_email: str = ""
    # brand → official domain overrides for logo resolution (JSON, e.g. {"BeOne":"beonemedicines.com"})
    brand_domains: str = ""
    pexels_api_key: str = ""

    # LLM — claude primary, Azure OpenAI is the fallback; per-stage overrides
    llm_provider: Literal["gpt", "claude"] = "claude"
    llm_provider_overrides: dict[str, str] = {}  # e.g. {"tagging": "gpt"}
    anthropic_api_key: str = ""
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_embed_deployment: str = "text-embedding-3-small"

    # Embeddings backend: "local" (fastembed ONNX, no API — default) or "azure".
    # The article_embeddings column dimension must match the active backend
    # (local bge-small = 384; azure text-embedding-3-small = 1536).
    embedding_backend: str = "local"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    local_embedding_dim: int = 384
    max_output_tokens: int = 32000
    llm_batch_size: int = 20
    llm_concurrency: int = 5

    # Retrieval — relevance floor on the FlashRank rerank score. Candidates below this
    # are treated as "no match" (chat refuses + suggests rephrasing) so the answer LLM is
    # never handed barely-relevant articles. Tunable per observed score distribution.
    retrieval_min_rerank: float = 0.10

    # Ingestion relevancy cut — drop articles whose embedding cosine to their subject's
    # company-framed anchor is below this. Calibrated on a real BeOne corpus (bge-small):
    # <0.50 is off-topic noise (sports/celebrity/scam/venue); >=0.50 is company/industry news.
    relevancy_threshold: float = 0.50

    # Guardrails
    nemo_rails_enabled: bool = True  # effective only when Azure OpenAI keys are present

    # Tracing (empty = disabled)
    langsmith_api_key: str = ""
    langsmith_project: str = "pr-intelligence-agent"
    logfire_token: str = ""

    # Connectors (empty key = connector disabled)
    searxng_url: str = "http://localhost:8083"
    serpapi_api_key: str = ""
    tavily_api_key: str = ""
    apify_token: str = ""
    apify_actor_search: str = "apify/google-search-scraper"
    apify_actor_rag: str = "apify/rag-web-browser"
    xpoz_api_key: str = ""
    xpoz_mcp_url: str = "https://mcp.xpoz.ai/mcp"
    similarweb_api_key: str = ""
    source_concurrency: int = 8

    # Execution
    max_concurrent_runs: int = 4
    run_event_retention_days: int = 30
    inbound_poll_seconds: int = 5          # email + teams chat/channel poll cadence
    collection_days_back: int = 2          # default news window (48h) per collection run

    # Pending-gate escalation: when a run is awaiting a human approval, re-notify at this
    # cadence up to gate_max_reminders times; if still no reply, drop (cancel) the run and
    # move on to other pending work. Keeps a stalled task from blocking a run slot forever.
    gate_escalation_enabled: bool = True
    gate_reminder_minutes: int = 30        # minutes between approval reminders
    gate_max_reminders: int = 2            # reminders after the first gate email, then drop

    # Self-healing
    self_heal_enabled: bool = True
    self_heal_browser: bool = False  # browser-assisted fix path (Phase C runtime)

    # Browser MCP (navigation for enrichment + self-heal; needs Node.js)
    browser_mcp_enabled: bool = False
    browser_mcp_command: str = "npx -y @agent360/browser-mcp"

    # Sandbox
    sandbox_backend: Literal["docker", "e2b"] = "docker"
    sandbox_container: str = "prsol-sandbox"
    e2b_api_key: str = ""

    # Channels
    teams_mcp_url: str = ""
    teams_token_path: str = "data/local/teams_token.json"
    teams_auth_callback_port: int = 8765
    # Real agent mailbox (the teams-mcp signed-in identity). Graph mail sends
    # from here; the fake agent@prsol.local was only ever the greenmail fallback.
    agent_email: str = "InfoVision.Agent1317@alphametricx.com"
    # "graph" → send outbound email from the real mailbox via teams-mcp mail_send;
    # "smtp" → local greenmail (offline tests). Graph is used only when teams-mcp
    # is enabled; otherwise we fall back to SMTP regardless of this value.
    email_delivery: str = "graph"
    # Trigger phrases the mention subscription watches for (comma-separated),
    # in addition to @Agent mentions.
    mention_keywords: str = ("@Agent,"
                             # generic trigger subjects — ANY brand, not a fixed list
                             "monitor,track,start monitoring,begin monitoring,"
                             "launch monitoring,PR monitoring,"
                             # gate replies + follow-up intents must surface too
                             "approve,approved,proceed,looks good,go ahead,"
                             "changes,revise,reject,RT")
    # Zero-touch onboarding: when a verified-domain sender triggers monitoring for a brand
    # that has no project yet, auto-provision it (industry self-resolved) instead of needing
    # a human to pre-create the project. Comma-separated domains; EMPTY = disabled (strict
    # stakeholder-only, no auto-provisioning). Sender's From is only trusted for this when
    # its domain is on this allowlist.
    authorized_sender_domains: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 3025
    imap_host: str = "localhost"
    imap_port: int = 3143


@lru_cache
def get_settings() -> Settings:
    return Settings()
