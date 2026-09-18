from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import sessionmaker

from .models import UserMemory


class MemoryRepository:
    """Persist explicit, user-scoped preference memories."""

    def __init__(self, database_url: str, *, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(database_url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def initialize_schema(self) -> None:
        from src.dmf_history.models import Base

        Base.metadata.create_all(self.engine)

    def list_active(
        self, *, tenant_id: str, user_id: str, limit: int = 8
    ) -> list[UserMemory]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.session_factory.begin() as session:
            memories = list(
                session.scalars(
                    select(UserMemory)
                    .where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.status == "active",
                    )
                    .order_by(UserMemory.updated_at.desc())
                    .limit(limit)
                )
            )
            now = datetime.now(timezone.utc)
            for memory in memories:
                memory.last_used_at = now
            return memories

    def remember(
        self,
        *,
        tenant_id: str,
        user_id: str,
        memory_key: str,
        content: dict,
        idempotency_key: str | None = None,
        audit_note: str | None = None,
    ) -> UserMemory:
        if not tenant_id or not user_id or not memory_key:
            raise ValueError("tenant_id, user_id and memory_key are required")
        if not isinstance(content, dict) or not content:
            raise ValueError("content must be a non-empty object")
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            if idempotency_key:
                existing = session.scalar(
                    select(UserMemory).where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return existing
            memory = session.scalar(
                select(UserMemory).where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.memory_key == memory_key,
                )
            )
            if memory is None:
                memory = UserMemory(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_key=memory_key,
                    content=content,
                    created_at=now,
                    updated_at=now,
                    idempotency_key=idempotency_key,
                    audit_note=audit_note,
                )
                session.add(memory)
            else:
                memory.content = content
                memory.status = "active"
                memory.deleted_at = None
                memory.updated_at = now
                memory.version += 1
                memory.idempotency_key = idempotency_key or memory.idempotency_key
                memory.audit_note = audit_note
            return memory

    def forget(
        self, *, tenant_id: str, user_id: str, memory_key: str
    ) -> bool:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            memory = session.scalar(
                select(UserMemory).where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.memory_key == memory_key,
                    UserMemory.status == "active",
                )
            )
            if memory is None:
                return False
            memory.status = "deleted"
            memory.deleted_at = now
            memory.updated_at = now
            memory.version += 1
            return True