from __future__ import annotations
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class ResearchState(TypedDict, total=False):
    """State of the research agent."""

    user_id: str
    user_query: str
    messages: Annotated[list[BaseMessage], add_messages]

    task_type: str
    requested_outputs: list[str]

    needs_clarification: bool
    clarification_question: str

    dmf_no: str
    applicant_name: str
    ingredients: list[str]

    dmf_results: dict[str, Any]
    analysis_result: dict[str, Any]

    final_answer: str
    warnings: list[str]
