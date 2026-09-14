from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ReactState(TypedDict, total=False):
    """Outer ReAct state with an explicit bridge to the research workflow."""

    user_id: str
    user_query: str
    request_id: str

    react_messages: Annotated[list[BaseMessage], add_messages]
    workflow_input: dict[str, Any]
    workflow_output: dict[str, Any]
    workflow_name: str
    workflow_status: str
    domain_pending: dict[str, Any]
    operation_ledger: dict[str, Any]
    completed_workflows: list[str]
    function_ledger: dict[str, Any]
    result_id: str
    result_source: str
    document_signature: str

    dmf_results: dict[str, Any]
    watchlist_result: dict[str, Any]
    pending_intent: dict[str, Any]
    pending_clarification_reason: str

    document_artifacts: dict[str, dict[str, Any]]
    document_extractions: dict[str, dict[str, Any]]
    document_errors: dict[str, str]
    merged_document_query: dict[str, Any]

    react_tool_context: str
    react_tool_rounds: int
    react_max_tool_rounds: int
    react_last_tool_batch: list[str]
    react_tool_stop_reason: str
    tool_artifacts: list[dict[str, Any]]

    workflow_tool_call_id: str
    final_answer: str
    warnings: list[str]