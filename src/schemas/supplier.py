"""Pydantic models for supplier document extraction."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SupplierInfo(BaseModel):
    """Normalized supplier information extracted from one or more documents."""

    source_file: str | None = None
    company_name: str | None = None
    product_name: str | None = None
    manufacturer: str | None = None
    address: str | None = None
    certificates: list[str] = Field(default_factory=list)
    dmf_information: list[str] = Field(default_factory=list)
    other_information: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
