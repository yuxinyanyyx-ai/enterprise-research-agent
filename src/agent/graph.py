from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    ask_clarification,
    build_dmf_answer,
    general_chat,
    handle_unknown,
    query_dmf,
    understand_request,
)
from src.agent.routes import route_after_intent
from src.agent.state import ResearchState


def build_research_graph():
    """构建 DMF Research Workflow。"""

    builder = StateGraph(ResearchState)

    builder.add_node("understand_request", understand_request)
    builder.add_node("query_dmf", query_dmf)
    builder.add_node("build_dmf_answer", build_dmf_answer)
    builder.add_node("general_chat", general_chat)
    builder.add_node("ask_clarification", ask_clarification)
    builder.add_node("handle_unknown", handle_unknown)

    builder.add_edge(START, "understand_request")

    builder.add_conditional_edges(
        "understand_request",
        route_after_intent,
        {
            "dmf_query": "query_dmf",
            "general_chat": "general_chat",
            "clarify": "ask_clarification",
            "unknown": "handle_unknown",
        },
    )

    # DMF 查询后的输出不再通过 Graph 分支选择，
    # 而是在 build_dmf_answer 中根据 requested_outputs 组合。
    builder.add_edge("query_dmf", "build_dmf_answer")
    builder.add_edge("build_dmf_answer", END)

    builder.add_edge("general_chat", END)
    builder.add_edge("ask_clarification", END)
    builder.add_edge("handle_unknown", END)

    return builder.compile()


research_graph = build_research_graph()
