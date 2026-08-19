from __future__ import annotations

from src.agent.state import ResearchState


def route_after_intent(state: ResearchState) -> str:
    """根据用户意图决定进入哪条业务流程。"""

    if state.get("needs_clarification"):
        return "clarify"

    task_type = state.get("task_type", "unknown")

    if task_type == "dmf_query":
        return "dmf_query"

    if task_type == "general_chat":
        return "general_chat"

    return "unknown"
