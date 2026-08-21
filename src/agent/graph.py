from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    ask_clarification,
    build_dmf_answer,
    confirm_document_query,
    extract_document_query,
    finalize_document_rejection,
    finalize_document_review,
    finalize_dmf_post_process,
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
from src.agent.tooling import (
    execute_tools,
    finalize_general_answer,
    invoke_dmf_tool_agent,
    invoke_general_tool_agent,
    mark_tool_limit,
    route_after_tool_agent,
    route_after_tool_execution,
)


def build_research_graph(checkpointer=None):
    """构建 DMF Research Workflow。"""

    builder = StateGraph(ResearchState)

    builder.add_node("understand_request", understand_request)
    builder.add_node("query_dmf", query_dmf)
    builder.add_node("build_dmf_answer", build_dmf_answer)
    builder.add_node("finalize_dmf_post_process", finalize_dmf_post_process)
    builder.add_node("ask_clarification", ask_clarification)
    builder.add_node("extract_document_query", extract_document_query)
    builder.add_node("confirm_document_query", confirm_document_query)
    builder.add_node("query_confirmed_document_dmf", query_confirmed_document_dmf)
    builder.add_node("finalize_document_review", finalize_document_review)
    builder.add_node("finalize_document_rejection", finalize_document_rejection)
    builder.add_node("general_tool_agent", invoke_general_tool_agent)
    builder.add_node("dmf_tool_agent", invoke_dmf_tool_agent)
    builder.add_node("dmf_post_process_agent", invoke_dmf_tool_agent)
    builder.add_node("execute_general_tools", execute_tools)
    builder.add_node("execute_dmf_tools", execute_tools)
    builder.add_node("execute_dmf_post_process", execute_tools)
    builder.add_node("finalize_general_answer", finalize_general_answer)
    builder.add_node("limit_general_tools", mark_tool_limit)
    builder.add_node("limit_dmf_tools", mark_tool_limit)
    builder.add_node("limit_dmf_post_process", mark_tool_limit)

    builder.add_edge(START, "understand_request")

    builder.add_conditional_edges(
        "understand_request",
        route_after_intent,
        {
            "dmf_query": "query_dmf",
            "dmf_post_process": "dmf_post_process_agent",
            "document_review": "extract_document_query",
            "document_query": "extract_document_query",
            "tools": "general_tool_agent",
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
    builder.add_edge("query_confirmed_document_dmf", "build_dmf_answer")

    builder.add_conditional_edges(
        "query_dmf",
        route_after_dmf_query,
        {
            "tools": "dmf_tool_agent",
            "answer": "build_dmf_answer",
        },
    )
    builder.add_conditional_edges(
        "dmf_tool_agent",
        route_after_tool_agent,
        {
            "execute": "execute_dmf_tools",
            "final": "build_dmf_answer",
            "limit": "limit_dmf_tools",
        },
    )
    builder.add_conditional_edges(
        "execute_dmf_tools",
        route_after_tool_execution,
        {
            "agent": "dmf_tool_agent",
            "final": "build_dmf_answer",
        },
    )
    builder.add_conditional_edges(
        "dmf_post_process_agent",
        route_after_tool_agent,
        {
            "execute": "execute_dmf_post_process",
            "final": "finalize_dmf_post_process",
            "limit": "limit_dmf_post_process",
        },
    )
    builder.add_conditional_edges(
        "execute_dmf_post_process",
        route_after_tool_execution,
        {
            "agent": "dmf_post_process_agent",
            "final": "finalize_dmf_post_process",
        },
    )
    builder.add_conditional_edges(
        "general_tool_agent",
        route_after_tool_agent,
        {
            "execute": "execute_general_tools",
            "final": "finalize_general_answer",
            "limit": "limit_general_tools",
        },
    )
    builder.add_conditional_edges(
        "execute_general_tools",
        route_after_tool_execution,
        {
            "agent": "general_tool_agent",
            "final": "finalize_general_answer",
        },
    )

    builder.add_edge("build_dmf_answer", END)
    builder.add_edge("limit_dmf_tools", "build_dmf_answer")
    builder.add_edge("limit_dmf_post_process", "finalize_dmf_post_process")
    builder.add_edge("finalize_dmf_post_process", END)
    builder.add_edge("limit_general_tools", "finalize_general_answer")
    builder.add_edge("finalize_general_answer", END)
    builder.add_edge("ask_clarification", END)
    builder.add_edge("finalize_document_review", END)
    builder.add_edge("finalize_document_rejection", END)

    return builder.compile(checkpointer=checkpointer)


research_graph = build_research_graph()
