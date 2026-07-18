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
    max_output_tokens: int = 32000
    llm_batch_size: int = 20
    llm_concurrency: int = 5

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
    xpoz_api_key: str = ""
    similarweb_api_key: str = ""

    # Execution
    max_concurrent_runs: int = 4
    run_event_retention_days: int = 30
    inbound_poll_seconds: int = 5          # email + teams chat/channel poll cadence

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
    smtp_host: str = "localhost"
    smtp_port: int = 3025
    imap_host: str = "localhost"
    imap_port: int = 3143
    agent_email: str = "agent@prsol.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
