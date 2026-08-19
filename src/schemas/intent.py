from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


OutputType = Literal[
    "result",
    "summary",
    "analysis",
]


class ResearchIntent(BaseModel):
    """LLM 对用户 DMF 调研请求的结构化理解结果。"""

    task_type: Literal[
        "dmf_query",
        "dmf_compare",
        "document_review",
        "dmf_document_compare",
        "general_chat",
        "unknown",
    ] = Field(
        description="用户的业务任务类型"
    )

    requested_outputs: list[OutputType] = Field(
        default_factory=list,
        description="用户最终明确希望看到的输出内容，可包含多个值",
    )

    dmf_no: str = Field(
        default="",
        description="用户明确提供的 DMF 编号",
    )

    applicant_name: str = Field(
        default="",
        description="用户明确提供的申请商名称",
    )

    ingredients: list[str] = Field(
        default_factory=list,
        description="用户明确提到的一个或多个成分名称",
    )

    needs_clarification: bool = Field(
        default=False,
        description="是否需要向用户补充询问信息",
    )

    clarification_question: str = Field(
        default="",
        description="需要补充信息时向用户提出的问题",
    )
