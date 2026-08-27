from __future__ import annotations

from src.agent.state import ResearchState


def route_after_intent(state: ResearchState) -> str:
    """根据用户意图决定进入哪条业务流程。"""

    if state.get("needs_clarification"):
        return "clarify"

    data_source = state.get("data_source", "none")
    if data_source == "none":
        return "general_chat"

    use_existing_data = state.get("use_existing_data", False)
    if data_source == "dmf":
        if not use_existing_data:
            return "dmf_query"
        return (
            "dmf_export"
            if "export" in (state.get("requested_outputs") or [])
            else "dmf_result_review"
        )

    query_document_conditions = state.get("query_document_conditions", False)
    if use_existing_data:
        if state.get("document_ids"):
            return "document_query" if query_document_conditions else "document_review"
        return (
            "document_followup"
            if query_document_conditions
            else "document_existing_review"
        )
    return "document_query" if query_document_conditions else "document_review"


def route_after_dmf_query(state: ResearchState) -> str:
    """Export only when explicitly requested; otherwise answer immediately."""

    if not (state.get("dmf_results") or {}).get("success"):
        return "answer"
    return (
        "export"
        if "export" in (state.get("requested_outputs") or [])
        else "answer"
    )


def route_after_document_extraction(state: ResearchState) -> str:
    if not state.get("merged_document_query"):
        return "review"
    return "confirm" if state.get("query_document_conditions") else "review"


def route_after_document_confirmation(state: ResearchState) -> str:
    return "query" if state.get("document_query_decision") in {"confirm", "edit"} else "reject"
