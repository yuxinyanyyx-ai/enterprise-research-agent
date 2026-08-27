"""Agent-facing tools backed by Apollo Studio Workflows."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.services.document_dmf_service import DocumentDMFService


@tool(
	"extract_document_dmf_params",
	description=(
		"使用 Apollo Studio 文档解析 Flow，从当前已上传文档中提取结构化的 "
		"DMF 查询条件。仅在系统存在活动文档时调用。"
	),
)
def extract_document_dmf_params(
	document_artifact: Annotated[dict[str, Any], InjectedToolArg],
) -> dict[str, Any]:
	"""Extract structured DMF query parameters from document Markdown."""

	return DocumentDMFService().extract_query(document_artifact).model_dump()
