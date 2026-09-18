from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.dmf_history.models import Base


class UserMemory(Base):
    __tablename__ = "agent_user_memories"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "memory_key", name="uq_agent_user_memory_key"
        ),
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "idempotency_key",
            name="uq_agent_user_memory_idempotency",
        ),
        Index(
            "ix_agent_user_memories_scope_status",
            "tenant_id",
            "user_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str] = mapped_column(String(255))
    memory_key: Mapped[str] = mapped_column(String(128))
    memory_type: Mapped[str] = mapped_column(String(32), default="preference")
    content: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32), default="explicit_user")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    audit_note: Mapped[str | None] = mapped_column(Text, nullable=True)