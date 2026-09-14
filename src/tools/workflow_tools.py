"""Agent-facing tools backed by Apollo Studio Workflows."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.services.document_dmf_service import DocumentDMFService
from src.tools.registry import (
	ToolContext,
	ToolKind,
	ToolRisk,
	register_tool,
)


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


@tool(
	"run_document_dmf_workflow",
	description=(
		"处理当前上传文档：提取条件、展示条件，或在人工确认后查询 DMF。"
		"不负责 Excel 导出；正常完成后可继续调用导出工具。每轮只能调用一个工具。"
	),
)
def run_document_dmf_workflow() -> str:
	"""Declare a graph handoff; the generic tool executor must not invoke it."""

	raise RuntimeError("Workflow handoff 必须由 LangGraph 专用子图路由执行")


register_tool(
	run_document_dmf_workflow,
	risk=ToolRisk.LOCAL_WRITE,
	contexts={ToolContext.GENERAL},
	kind=ToolKind.WORKFLOW_HANDOFF,
	parallel_safe=False,
)


@tool("run_watchlist_workflow", description="管理单个团队 DMF 关注项、查看事件、配置通知或触发检查。邮件由后台发送，不支持批量操作或立即发信。")
def run_watchlist_workflow() -> str:
	"""Declare a statically routed Watchlist handoff."""
	raise RuntimeError("Workflow handoff 必须由 LangGraph 专用子图路由执行")


register_tool(
	run_watchlist_workflow,
	risk=ToolRisk.LOCAL_WRITE,
	contexts={ToolContext.GENERAL},
	kind=ToolKind.WORKFLOW_HANDOFF,
	parallel_safe=False,
)
