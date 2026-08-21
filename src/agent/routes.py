from __future__ import annotations

from src.agent.state import ResearchState


def route_after_intent(state: ResearchState) -> str:
    """根据用户意图决定进入哪条业务流程。"""

    if state.get("needs_clarification"):
        return "clarify"

    task_type = state.get("task_type", "unknown")

    if task_type == "dmf_query":
        return "dmf_query"

    if task_type == "dmf_post_process":
        return "dmf_post_process"

    if task_type == "document_review":
        return "document_review"

    if task_type == "dmf_document_compare":
        return "document_query"

    return "tools"


def route_after_dmf_query(state: ResearchState) -> str:
    """Only successful fixed DMF searches may use post-processing tools."""

    return "tools" if (state.get("dmf_results") or {}).get("success") else "answer"


def route_after_document_extraction(state: ResearchState) -> str:
    if state.get("document_error"):
        return "review"
    return "confirm" if state.get("task_type") == "dmf_document_compare" else "review"


def route_after_document_confirmation(state: ResearchState) -> str:
    return "query" if state.get("document_query_decision") in {"confirm", "edit"} else "reject"
