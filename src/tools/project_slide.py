"""Agent tools for generating and modifying Project Slides."""

from __future__ import annotations

import re
from typing import Annotated

from langchain_core.tools import InjectedToolArg
from langchain_core.tools import tool

from src.tools.registry import (
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolRisk,
    ToolState,
    register_tool,
)


@tool(
    "generate_project_slide",
    description=(
        "根据当前 Agent 会话中上传的 PPTX（Status/Risk 与 Milestone 数据页）"
        "生成 Project Slide 第 1 页，并返回下载链接。"
    ),
)
def generate_project_slide(
    file_id: str,
    session_id: Annotated[str, InjectedToolArg],
) -> str:
    """根据用户上传的 PPTX 数据文件生成 Project Slide 第 1 页，并返回下载链接。

    数据文件应含 Status/Risk 页与 Milestone 页（2 页即可，无需第 1 页）。
    file_id: 当前 Agent 会话上传接口返回的文件标识符。
    """
    from src.services.slide_generator_services import (
        format_generation_result,
        generate_for_file_id,
    )

    payload = generate_for_file_id(session_id, file_id)
    return format_generation_result(payload)


@tool(
    "modify_project_slide",
    description=(
        "根据用户反馈修改当前 Agent 会话最近生成的 Project Slide，"
        "返回修改版下载链接。"
    ),
)
def modify_project_slide(
    job_id: str,
    feedback: str,
    session_id: Annotated[str, InjectedToolArg],
) -> str:
    """修改之前 generate_project_slide 生成的 Project Slide，并返回新的下载链接。

    可修改 timeline 文本（日期、备注、study label）、底部 Status/Next/Risk 表格和标题。
    不增删 milestone，不改变总体结构。

    job_id: 上一版生成结果下载链接中的 32 位 hex 标识符。
    feedback: 用户的修改要求，请尽量原文传入。
    """
    from src.services.slide_generator_services import (
        format_modification_result,
        modify_for_job_id,
    )

    payload = modify_for_job_id(session_id, job_id, feedback)
    return format_modification_result(payload)


def authorize_project_slide(state: ToolState, call: ToolCall) -> str | None:
    query = str(state.get("user_query", ""))
    if not re.search(
        r"project\s*slide|项目页|项目幻灯片|pptx?|timeline|里程碑",
        query,
        re.IGNORECASE,
    ) or not re.search(
        r"生成|制作|创建|修改|调整|更新|导出|下载|generate|create|modify|edit|download",
        query,
        re.IGNORECASE,
    ):
        return "只有用户明确请求生成或修改 Project Slide 时才能执行。"
    if not state.get("agent_session_id"):
        return "缺少当前 Agent 会话上下文，无法操作 Project Slide。"
    if call.get("name") == "modify_project_slide" and not state.get(
        "latest_project_slide_job_id"
    ):
        requested_job = str((call.get("args") or {}).get("job_id", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", requested_job):
            return "当前会话没有可修改的 Project Slide，请先生成一份。"
    return None


def adapt_project_slide_result(state: ToolState, result: object) -> dict:
    match = re.search(
        r"/project-slides/([0-9a-f]{32})(?:[/?#)]|$)", str(result)
    )
    return {"latest_project_slide_job_id": match.group(1)} if match else {}


def register_project_slide_tools(registry: ToolRegistry) -> None:
    for project_slide_tool in (generate_project_slide, modify_project_slide):
        register_tool(
            project_slide_tool,
            registry=registry,
            risk=ToolRisk.LOCAL_WRITE,
            contexts={ToolContext.GENERAL},
            parallel_safe=False,
            state_arguments={"session_id": "agent_session_id"},
            authorize=authorize_project_slide,
            result_adapter=adapt_project_slide_result,
        )
