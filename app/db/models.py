import uuid
from datetime import date, datetime, time

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

UUID_PK = dict(
    primary_key=True,
    server_default=text("gen_random_uuid()"),
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="analyst")
    api_key_hash: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint("role IN ('admin','analyst','viewer')", name="ck_users_role"),
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(200))
    section_taxonomy: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
        server_default=text('\'["Brand News", "Competitors News", "Industry News"]\'::jsonb'),
    )
    stakeholder_emails: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="created")
    error: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    source_file_key: Mapped[str | None] = mapped_column(String(300))
    tagged_file_key: Mapped[str | None] = mapped_column(String(300))
    charts_data_file_key: Mapped[str | None] = mapped_column(String(300))
    articles_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "status IN ('created','ingesting','ingested','awaiting_gate1','tagging','tagged',"
            "'awaiting_gate2','in_review','reviewed','charts_ready','failed')",
            name="ck_sessions_status",
        ),
        Index("ix_sessions_project_id", "project_id"),
    )


class GeneratedQuery(Base, TimestampMixin):
    __tablename__ = "generated_queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    brand: Mapped[str] = mapped_column(String(200), nullable=False)
    query_groups: Mapped[dict] = mapped_column(JSONB, nullable=False)
    competitors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    schedule_time: Mapped[time | None] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    last_run_date: Mapped[date | None] = mapped_column(Date)
    last_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"))


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    json_data: Mapped[dict | None] = mapped_column(JSONB)
    binary_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "(json_data IS NULL) != (binary_data IS NULL)", name="ck_artifacts_one_payload"
        ),
    )


class CorrectionEvent(Base, TimestampMixin):
    __tablename__ = "correction_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    article_id: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str] = mapped_column(String(60), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    original_confidence: Mapped[float | None] = mapped_column(Float)
    article_features: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="edit")

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('edit','delete','add','approve')", name="ck_correction_event_type"
        ),
        Index("ix_corrections_project_created", "project_id", "created_at"),
    )


class AgentMemory(Base, TimestampMixin):
    __tablename__ = "agent_memory"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    memory_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "memory_type IN ('brand_context','user_spec','query_history','session_summary')",
            name="ck_agent_memory_type",
        ),
        Index("ix_agent_memory_project_type", "project_id", "memory_type"),
    )


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    prefs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ArticleEmbedding(Base, TimestampMixin):
    __tablename__ = "article_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    article_id: Mapped[str] = mapped_column(String(20), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    section: Mapped[str | None] = mapped_column(String(120))
    sentiment: Mapped[str | None] = mapped_column(String(3))
    published_at: Mapped[date | None] = mapped_column(Date)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_approved_for_monitoring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    content_preview: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        Index("ix_article_embeddings_scope", "project_id", "session_id"),
        Index(
            "ix_article_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class LLMCall(Base, TimestampMixin):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    request_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(40))
    purpose: Mapped[str | None] = mapped_column(String(80))
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ok")
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_llm_calls_created", "created_at"),)


class GuardrailEvent(Base, TimestampMixin):
    __tablename__ = "guardrail_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    request_id: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    check_name: Mapped[str] = mapped_column(String(60), nullable=False)
    verdict: Mapped[str] = mapped_column(String(15), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint("direction IN ('input','output')", name="ck_guardrail_direction"),
        CheckConstraint("verdict IN ('pass','sanitized','blocked')", name="ck_guardrail_verdict"),
    )


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"))
    graph_name: Mapped[str] = mapped_column(String(60), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    origin_channel: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    origin_address: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    awaiting_input: Mapped[dict | None] = mapped_column(JSONB)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','paused','interrupted','awaiting_human','failed',"
            "'completed','cancelled')",
            name="ck_runs_status",
        ),
        CheckConstraint(
            "origin_channel IN ('web','email','teams_chat','teams_channel','scheduler')",
            name="ck_runs_origin_channel",
        ),
        Index("ix_runs_status", "status"),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    node: Mapped[str | None] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
