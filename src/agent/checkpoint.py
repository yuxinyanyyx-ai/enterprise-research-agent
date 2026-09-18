from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from src.settings import Settings


class AgentCheckpoint(AbstractContextManager[Any]):
    """Own the lifetime of an optional PostgreSQL LangGraph checkpointer."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._context: AbstractContextManager[Any] | None = None
        self._checkpointer: Any = None

    def __enter__(self) -> Any:
        if not self.settings.agent_checkpoint_enabled:
            from langgraph.checkpoint.memory import InMemorySaver

            self._checkpointer = InMemorySaver()
            return self._checkpointer
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise RuntimeError(
                "AGENT_CHECKPOINT_ENABLED=true requires langgraph-checkpoint-postgres"
            ) from exc
        self._context = PostgresSaver.from_conn_string(self.settings.database_url)
        self._checkpointer = self._context.__enter__()
        self._checkpointer.setup()
        return self._checkpointer

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        if self._context is not None:
            return self._context.__exit__(exc_type, exc_value, traceback)
        return None