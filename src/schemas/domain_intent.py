from pydantic import BaseModel, ConfigDict, Field

from src.schemas.intent import WatchlistAction
from typing import Literal


class DocumentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[str] = Field(default_factory=list)
    use_existing_data: bool = False
    query_document_conditions: bool = False
    needs_clarification: bool = False


class WatchlistIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    watchlist_action: WatchlistAction | None = None
    watchlist_id: str = ""
    dmf_no: str = ""
    watchlist_event_id: str = ""
    watchlist_interval_hours: int | None = None
    watchlist_notification_enabled: bool | None = None
    watchlist_notification_emails: list[str] | None = None
    watchlist_notification_mode: Literal["immediate", "weekly_digest"] | None = None
    needs_clarification: bool = False