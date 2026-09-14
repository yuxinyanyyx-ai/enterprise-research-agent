from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


OutputType = Literal[
    "result",
    "summary",
    "analysis",
    "export",
]
WatchlistAction = Literal["add", "remove", "list", "run", "events", "ack", "configure_notifications"]


class ResearchIntent(BaseModel):
    """LLM 对用户 DMF 调研请求的结构化理解结果。"""

    data_source: Literal["dmf", "document", "watchlist", "none"] = Field(
        description="完成请求需要使用的数据来源"
    )

    watchlist_action: WatchlistAction | None = Field(
        default=None,
        description="团队关注清单操作",
    )

    watchlist_id: str = Field(default="", description="明确提供的关注项 ID")

    watchlist_event_id: str = Field(default="", description="明确提供的关注事件 ID")
    watchlist_notification_enabled: bool | None = Field(default=None, description="明确开启或关闭邮件通知；未修改为 null")
    watchlist_notification_emails: list[str] | None = Field(default=None, description="用户明确指定的完整收件邮箱列表；未修改为 null，清空为 []")
    watchlist_notification_mode: Literal["immediate", "weekly_digest"] | None = Field(default=None, description="有更新通知为 immediate；每周邮件汇总为 weekly_digest；未修改为 null")

    watchlist_interval_hours: int | None = Field(
        default=None,
        description="添加关注项时明确提供的检查间隔小时数",
    )

    use_existing_data: bool = Field(
        default=False,
        description="是否复用当前会话已有的 DMF 结果或文档提取条件",
    )

    requested_outputs: list[OutputType] = Field(
        default_factory=list,
        description="用户希望得到的结果、摘要、分析或导出，可包含多个值",
    )

    query_document_conditions: bool = Field(
        default=False,
        description="是否需要使用文档中提取的条件继续查询 DMF",
    )

    document_ids: list[str] = Field(
        default_factory=list,
        description="需要使用的当前会话文档 ID；空列表表示全部活动文档",
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
        description="用户语义本身是否不完整或存在歧义",
    )
