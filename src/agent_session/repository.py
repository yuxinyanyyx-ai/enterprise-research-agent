from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import sessionmaker

from .models import AgentSession


class AgentSessionRepository:
    """Persist the minimal metadata needed to recover an Agent session."""

    def __init__(self, database_url: str, *, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(database_url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def initialize_schema(self) -> None:
        from src.dmf_history.models import Base

        Base.metadata.create_all(self.engine)

    def create(
        self,
        *,
        session_id: str,
        user_id: str = "",
        tenant_id: str = "",
        expires_at: datetime | None = None,
    ) -> AgentSession:
        if not session_id:
            raise ValueError("session_id is required")
        now = datetime.now(timezone.utc)
        session = AgentSession(
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        with self.session_factory.begin() as db_session:
            db_session.add(session)
        return session

    def get(
        self,
        session_id: str,
        *,
        user_id: str = "",
        tenant_id: str = "",
    ) -> AgentSession | None:
        with self.session_factory() as db_session:
            statement = select(AgentSession).where(
                AgentSession.session_id == session_id,
                AgentSession.user_id == user_id,
                AgentSession.tenant_id == tenant_id,
            )
            return db_session.scalar(statement)

    def update_awaiting_resume(
        self,
        session_id: str,
        *,
        awaiting_resume: bool,
        user_id: str = "",
        tenant_id: str = "",
    ) -> AgentSession | None:
        with self.session_factory.begin() as db_session:
            session = db_session.scalar(
                select(AgentSession).where(
                    AgentSession.session_id == session_id,
                    AgentSession.user_id == user_id,
                    AgentSession.tenant_id == tenant_id,
                )
            )
            if session is None:
                return None
            session.awaiting_resume = awaiting_resume
            session.updated_at = datetime.now(timezone.utc)
            return session
