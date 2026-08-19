"""Pydantic models for DMF query data."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    total: int = 0
    total_pages: int = 0
    current_page: int | None = None
    page_size: int | None = None
    records: list[DMFRecord] = Field(default_factory=list)


class DMFSearchResult(BaseModel):
    """Stable result contract returned by the DMF business service/tool."""

    success: bool
    message: str
    query: DMFQuery
    query_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    total_records: int = 0
    results: list[DMFQueryResult] = Field(default_factory=list)
