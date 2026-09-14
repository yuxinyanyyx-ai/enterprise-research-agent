from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkflowResult(BaseModel):
    workflow_name: Literal["document", "watchlist"]
    status: Literal["completed", "needs_clarification", "cancelled", "failed", "rejected"]
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def can_continue(self) -> bool:
        return self.status == "completed"