"""Pydantic models for DMF query data."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DMFCollectionStatus(StrEnum):
    """Completeness of one concrete upstream query collection."""

    SUCCESS_NONEMPTY = "SUCCESS_NONEMPTY"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"


class DMFFieldChange(BaseModel):
    """One normalized field difference for a stable DMF identity."""

    field: str
    before: str | None = None
    after: str | None = None


class DMFChangeEvent(BaseModel):
    """One detected change between two eligible snapshots."""

    change_type: str
    business_key: str
    changes: list[DMFFieldChange] = Field(default_factory=list)


class DMFHistoryResult(BaseModel):
    """History processing outcome attached without changing query success."""

    monitor_run_id: str | None = None
    snapshot_id: str | None = None
    history_status: str = "not_processed"
    comparison_status: str = "not_processed"
    skip_reason: str | None = None
    baseline_snapshot_id: str | None = None
    baseline_created: bool = False
    added_count: int = 0
    removed_count: int = 0
    changed_count: int = 0
    unmatched_record_count: int = 0
    ambiguous_business_keys: list[str] = Field(default_factory=list)
    events: list[DMFChangeEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    history_error_code: str | None = None
    history_error: str | None = None


class DMFRecord(BaseModel):
    """One normalized DMF record returned by the upstream service."""

    dmf_no: str | None = None
    applicant_name: str | None = None
    ingredient: str | None = None
    valid_date: str | None = None


class DMFSingleQuery(BaseModel):
    """The concrete conditions used for one upstream DMF query."""

    dmf_no: str = ""
    applicant_name: str = ""
    ingredient: str = ""


class DMFQuery(BaseModel):
    """Top-level query conditions accepted by the Agent-facing service."""

    dmf_no: str = ""
    applicant_name: str = ""
    ingredients: list[str] = Field(default_factory=list)


class DMFQueryResult(BaseModel):
    """Result of one concrete DMF query, including all fetched pages."""

    success: bool
    message: str
    query: DMFSingleQuery
    collection_status: DMFCollectionStatus | None = None
    total: int = 0
    total_pages: int = 0
    current_page: int | None = None
    page_size: int | None = None
    successful_pages: int = 0
    failed_page: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    queried_at: datetime | None = None
    records: list[DMFRecord] = Field(default_factory=list)
    history: DMFHistoryResult | None = None


class DMFSearchResult(BaseModel):
    """Stable result contract returned by the DMF business service/tool."""

    success: bool
    message: str
    query: DMFQuery
    query_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    not_executed_count: int = 0
    total_records: int = 0
    results: list[DMFQueryResult] = Field(default_factory=list)
