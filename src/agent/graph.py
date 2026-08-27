from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    ask_clarification,
    build_dmf_answer,
    confirm_document_query,
    extract_document_query,
    export_dmf_results,
    finalize_document_rejection,
    finalize_document_review,
    finalize_dmf_export,
    general_chat,
    query_dmf,
    query_confirmed_document_dmf,
    understand_request,
)
from src.agent.routes import (
    route_after_dmf_query,
    route_after_document_confirmation,
    route_after_document_extraction,
    route_after_intent,
)
from src.agent.state import ResearchState


def build_research_graph(checkpointer=None):
    """构建 DMF Research Workflow。"""

    builder = StateGraph(ResearchState)

    builder.add_node("understand_request", understand_request)
    builder.add_node("query_dmf", query_dmf)
    builder.add_node("build_dmf_answer", build_dmf_answer)
    builder.add_node("export_dmf_results", export_dmf_results)
    builder.add_node("export_existing_dmf_results", export_dmf_results)
    builder.add_node("finalize_dmf_export", finalize_dmf_export)
    builder.add_node("ask_clarification", ask_clarification)
    builder.add_node("extract_document_query", extract_document_query)
    builder.add_node("confirm_document_query", confirm_document_query)
    builder.add_node("query_confirmed_document_dmf", query_confirmed_document_dmf)
    builder.add_node("finalize_document_review", finalize_document_review)
    builder.add_node("finalize_document_rejection", finalize_document_rejection)
    builder.add_node("general_chat", general_chat)

    builder.add_edge(START, "understand_request")

    builder.add_conditional_edges(
        "understand_request",
        route_after_intent,
        {
            "dmf_query": "query_dmf",
            "dmf_export": "export_existing_dmf_results",
            "dmf_result_review": "build_dmf_answer",
            "document_followup": "confirm_document_query",
            "document_existing_review": "finalize_document_review",
            "document_review": "extract_document_query",
            "document_query": "extract_document_query",
            "general_chat": "general_chat",
            "clarify": "ask_clarification",
        },
    )

    builder.add_conditional_edges(
        "extract_document_query",
        route_after_document_extraction,
        {
            "review": "finalize_document_review",
            "confirm": "confirm_document_query",
        },
    )
    builder.add_conditional_edges(
        "confirm_document_query",
        route_after_document_confirmation,
        {
            "query": "query_confirmed_document_dmf",
            "reject": "finalize_document_rejection",
        },
    )
    builder.add_conditional_edges(
        "query_confirmed_document_dmf",
        route_after_dmf_query,
        {
            "export": "export_dmf_results",
            "answer": "build_dmf_answer",
        },
    )

    builder.add_conditional_edges(
        "query_dmf",
        route_after_dmf_query,
        {
            "export": "export_dmf_results",
            "answer": "build_dmf_answer",
        },
    )
    builder.add_edge("export_dmf_results", "build_dmf_answer")
    builder.add_edge("export_existing_dmf_results", "finalize_dmf_export")
    builder.add_edge("build_dmf_answer", END)
    builder.add_edge("finalize_dmf_export", END)
    builder.add_edge("general_chat", END)
    builder.add_edge("ask_clarification", END)
    builder.add_edge("finalize_document_review", END)
    builder.add_edge("finalize_document_rejection", END)

    return builder.compile(checkpointer=checkpointer)


research_graph = build_research_graph()
