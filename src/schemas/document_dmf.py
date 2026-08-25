"""Contracts for the document-driven DMF workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DocumentArtifact(BaseModel):
	"""A parsed document whose Markdown is stored in a trusted directory."""

	document_id: str
	file_name: str
	source_path: str
	markdown_path: str
	status: Literal["parsed"] = "parsed"


class ExtractedDMFQuery(BaseModel):
	"""Normalized DMF conditions extracted from a document."""

	dmf_no: str = ""
	applicant_name: str = ""
	ingredients: list[str] = Field(default_factory=list)

	@field_validator("dmf_no", "applicant_name", mode="before")
	@classmethod
	def normalize_text(cls, value: object) -> str:
		return str(value or "").strip()

	@field_validator("ingredients", mode="before")
	@classmethod
	def normalize_ingredients(cls, value: object) -> list[str]:
		if value is None:
			return []
		if isinstance(value, str):
			value = [value]
		if not isinstance(value, (list, tuple, set)):
			raise ValueError("ingredients 必须是字符串列表")
		seen: set[str] = set()
		result: list[str] = []
		for item in value:
			normalized = str(item or "").strip()
			key = normalized.casefold()
			if normalized and key not in seen:
				seen.add(key)
				result.append(normalized)
		return result

	@model_validator(mode="after")
	def require_condition(self) -> "ExtractedDMFQuery":
		if not (self.dmf_no or self.applicant_name or self.ingredients):
			raise ValueError("文档中未提取到可用的 DMF 查询条件")
		return self


class ExtractedDMFQueryBatch(BaseModel):
	"""Independent DMF conditions extracted from document rows."""

	queries: list[ExtractedDMFQuery]

	@model_validator(mode="before")
	@classmethod
	def accept_legacy_single_query(cls, value: object) -> object:
		if isinstance(value, cls):
			return value
		if isinstance(value, ExtractedDMFQuery):
			return {"queries": [value]}
		if isinstance(value, dict) and "queries" not in value:
			return {"queries": [value]}
		return value

	@field_validator("queries")
	@classmethod
	def require_unique_queries(
		cls,
		value: list[ExtractedDMFQuery],
	) -> list[ExtractedDMFQuery]:
		if not value:
			raise ValueError("文档中未提取到可用的 DMF 查询条件")
		seen: set[tuple[str, str, tuple[str, ...]]] = set()
		result: list[ExtractedDMFQuery] = []
		for query in value:
			key = (
				query.dmf_no.casefold(),
				query.applicant_name.casefold(),
				tuple(item.casefold() for item in query.ingredients),
			)
			if key not in seen:
				seen.add(key)
				result.append(query)
		return result


class DocumentQueryDecision(BaseModel):
	"""User decision for extracted conditions before a real DMF query."""

	action: Literal["confirm", "edit", "reject"]
	query: ExtractedDMFQueryBatch | None = None

	@model_validator(mode="after")
	def require_query_for_execution(self) -> "DocumentQueryDecision":
		if self.action in {"confirm", "edit"} and self.query is None:
			raise ValueError("确认或修改查询时必须提供 query")
		return self